# Transaction Webhook Service

Receives transaction webhooks, ACKs with `202` immediately, and processes each
transaction in the background (30s simulated delay). Results are stored in
Postgres. Duplicate `transaction_id`s are processed once.

Public URL: `<fill in after deploy>`

## API

| Method | Path | Notes |
|---|---|---|
| `POST` | `/v1/webhooks/transactions` | `202` for new and duplicate deliveries. `422` on a bad payload. `503` if the row couldn't be stored (sender should retry). |
| `GET` | `/v1/transactions/{transaction_id}` | Returns a one-element list, `404` if unknown. `status` is `PROCESSING`, `PROCESSED`, or `FAILED`. |
| `GET` | `/` | Health check. |

Payload:

```json
{
  "transaction_id": "txn_abc123def456",
  "source_account": "acc_user_789",
  "destination_account": "acc_merchant_456",
  "amount": 1500,
  "currency": "INR"
}
```

`amount` must be positive with at most 2 decimals; `currency` is a 3-letter uppercase code.

## Run locally

Needs Python 3.11+ and Docker (for Postgres).

```bash
docker compose up -d db
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
export DATABASE_URL=postgresql://postgres:postgres@localhost:5432/transactions
.venv/bin/uvicorn app.main:app --port 8000
```

Or everything in containers: `docker compose up --build`.

Config is via env vars, see [.env.example](.env.example). The table is created on startup.

## Try it

```bash
curl -i -X POST localhost:8000/v1/webhooks/transactions \
  -H 'content-type: application/json' \
  -d '{"transaction_id":"txn_1","source_account":"a","destination_account":"b","amount":1500,"currency":"INR"}'

curl localhost:8000/v1/transactions/txn_1   # PROCESSING
sleep 31
curl localhost:8000/v1/transactions/txn_1   # PROCESSED, processed_at ~30s after created_at
```

Send the same POST several times; there is still one row and `processed_at` is set once.

## Tests

```bash
docker compose up -d db
.venv/bin/python -m pytest
```

The suite uses a real Postgres and a 1s processing delay. It covers latency, the
PROCESSING -> PROCESSED transition, concurrent duplicates, duplicates after
completion, validation, recovery of an orphaned row, and a poison row going to `FAILED`.

## Deploy

A [Dockerfile](Dockerfile) and a Render blueprint ([render.yaml](render.yaml)) are included.
Any managed Postgres works (Neon, Supabase, Render Postgres); set `DATABASE_URL` on the
service. Deploy in the same region as the database: from a laptop in another continent
the insert alone took ~1s, which is over the 500ms budget. Use an always-on instance: a host that sleeps when idle will blow the 500ms
target on the first request and pauses background processing.

## Technical choices

Short version here; the reasoning and trade-offs are in [docs/design.md](docs/design.md).

- **FastAPI + asyncpg.** The webhook path is one indexed insert, so the response
  time is a few ms. No ORM; there is one table and four queries.
- **Postgres as the queue.** The row is written before the 202, so an acknowledged
  webhook can't be lost. The same table is polled by the worker, which avoids
  running a separate broker for this volume.
- **Idempotency via primary key** (`INSERT ... ON CONFLICT DO NOTHING`), not a
  read-then-write check, so concurrent duplicates can't race.
- **Leases + `SKIP LOCKED`** so multiple instances can run safely and a crashed
  instance's work is picked up again.
