# Design notes

## Requirements that shaped the design

1. ACK in <500ms regardless of processing time.
2. Don't lose a transaction once ACKed, including across restarts.
3. Same `transaction_id` -> one processed transaction, no errors.
4. 30s processing delay, result persisted.

## Request flow

```
POST /v1/webhooks/transactions
  -> validate (pydantic)
  -> INSERT ... ON CONFLICT (transaction_id) DO NOTHING
  -> 202

worker loop (every ~1s, per instance)
  -> mark rows that hit MAX_ATTEMPTS as FAILED
  -> claim up to N unfinished rows (lease them)
  -> per row: sleep until created_at + 30s, then set PROCESSED + processed_at
```

The handler never waits on processing. It does one insert and returns.

## Data model

One table, `transactions`. Besides the fields from the spec it has
`status`, `attempts`, `locked_until`, `created_at`, `processed_at`.

- `transaction_id` is the primary key; that is the idempotency guarantee.
- `amount` is `NUMERIC(18,2)`, not float. It is serialised as a JSON number to match
  the contract.
- A partial index on `created_at WHERE status = 'PROCESSING'` keeps the worker's
  query cheap as processed rows pile up.

## Idempotency

The first webhook inserts the row. Any later one with the same id hits the conflict
and does nothing; the handler still returns 202 so the sender sees success and stops
retrying. Because the check is the insert itself, two simultaneous duplicates
cannot both win.

First write wins: a duplicate with a different amount does not overwrite the stored
one. A real payment integration might want to log or alert on a mismatch; here it is
only logged as a duplicate.

## Reliability

- **Durable before ACK.** If the insert fails (DB down, pool timeout) the handler
  returns 503, not 202. Processors like Razorpay retry on non-2xx, so nothing is
  silently dropped. The pool has a 5s acquire/command timeout so a sick DB can't hang
  requests indefinitely.
- **Leases.** When a worker claims a row it sets `locked_until = now() + lease`
  (processing delay + 30s). `FOR UPDATE SKIP LOCKED` means instances never block or
  double-claim. If an instance dies, the lease expires and another claim picks the row
  up.
- **Graceful shutdown** releases the leases of in-flight rows so a normal deploy or
  restart doesn't wait for expiry. A hard kill (`kill -9`, OOM) does wait: recovery is
  up to `lease` seconds (60s by default). I checked this by killing the process
  mid-processing; the row finished after the lease expired.
- **Delay measured from `created_at`.** On claim the worker computes the remaining
  delay in SQL. A row recovered after an outage is not made to wait another 30s, and
  poll jitter does not stretch the delay.
- **Poison rows.** `attempts` increments on every claim. After `MAX_ATTEMPTS` the row
  is marked `FAILED` instead of looping forever. With the simulated sleep this should
  not happen in practice; it matters once the step calls a real API.
- **`mark_processed` is conditional** (`WHERE status = 'PROCESSING'`), so a late
  finisher can't overwrite a terminal state.

Processing is at-least-once with an idempotent final write. If the real step had
external side effects, it would need its own idempotency key (the `transaction_id`).

## Scaling

The sleep is `asyncio.sleep`, so one instance holds hundreds of in-flight
transactions (`WORKER_CONCURRENCY`, default 200). Throughput per instance is roughly
concurrency / delay, about 6-7 transactions per second at the defaults. Raise the
concurrency or add instances; every instance runs the API and the worker, and the
lease protocol keeps them from colliding.

If volume grew well past that, the next step is to split the worker into its own
service and/or move to a broker (SQS, Cloud Tasks). I did not do it here because it
adds moving parts without helping the requirements.

## Things deliberately left out

- **Webhook signature verification.** Razorpay-style senders sign the body; verifying
  that (HMAC with a shared secret) is the first thing to add before real traffic.
  The spec doesn't define a scheme, so it isn't implemented.
- **Migrations tool.** The schema is a single `CREATE TABLE IF NOT EXISTS` run under
  an advisory lock at startup. Alembic would be the move once the schema changes.
- **Cleanup/retention** of old rows.
- **Auth on the query endpoint.** The spec labels it "for testing"; it should not be
  public in a real deployment.

## Operational notes

- Free-tier hosts that sleep on idle break the latency target and pause the worker.
  Work resumes correctly on wake because state is in the DB.
- Pooled Postgres endpoints (pgbouncer) are supported: asyncpg's statement cache is
  disabled.
- Logs include transaction ids only, not account numbers or amounts.
