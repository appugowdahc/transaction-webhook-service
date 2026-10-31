import time
from concurrent.futures import ThreadPoolExecutor

PAYLOAD = {
    "transaction_id": "txn_abc123def456",
    "source_account": "acc_user_789",
    "destination_account": "acc_merchant_456",
    "amount": 1500,
    "currency": "INR",
}
URL = "/v1/webhooks/transactions"


def wait_for_status(client, txn_id, want, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/v1/transactions/{txn_id}").json()
        if body[0]["status"] == want:
            return body[0]
        time.sleep(0.2)
    raise AssertionError(f"{txn_id} never reached {want}")


def test_health(client):
    r = client.get("/")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "HEALTHY"
    assert body["current_time"].endswith("Z")


def test_webhook_is_fast_and_processed_after_delay(client):
    start = time.perf_counter()
    r = client.post(URL, json=PAYLOAD)
    elapsed = time.perf_counter() - start
    assert r.status_code == 202
    assert elapsed < 0.5

    first = client.get(f"/v1/transactions/{PAYLOAD['transaction_id']}").json()[0]
    assert first["status"] == "PROCESSING"
    assert first["processed_at"] is None
    assert first["amount"] == 1500

    done = wait_for_status(client, PAYLOAD["transaction_id"], "PROCESSED")
    assert done["processed_at"] is not None


def test_duplicates_create_one_transaction(client, sql):
    with ThreadPoolExecutor(8) as pool:
        codes = list(pool.map(lambda _: client.post(URL, json=PAYLOAD).status_code, range(8)))
    assert codes == [202] * 8

    wait_for_status(client, PAYLOAD["transaction_id"], "PROCESSED")
    rows = sql("SELECT attempts FROM transactions", fetch=True)
    assert len(rows) == 1
    assert rows[0]["attempts"] == 1  # processed once, not once per webhook


def test_duplicate_after_processing_does_not_reprocess(client):
    client.post(URL, json=PAYLOAD)
    done = wait_for_status(client, PAYLOAD["transaction_id"], "PROCESSED")

    assert client.post(URL, json={**PAYLOAD, "amount": 1}).status_code == 202
    time.sleep(1.5)
    again = client.get(f"/v1/transactions/{PAYLOAD['transaction_id']}").json()[0]
    assert again["processed_at"] == done["processed_at"]
    assert again["amount"] == 1500  # first write wins


def test_invalid_payload_rejected(client):
    assert client.post(URL, json={**PAYLOAD, "amount": -5}).status_code == 422
    assert client.post(URL, json={**PAYLOAD, "currency": "rupees"}).status_code == 422
    assert client.post(URL, json={"transaction_id": "x"}).status_code == 422


def test_unknown_transaction_404(client):
    assert client.get("/v1/transactions/nope").status_code == 404


def test_orphaned_row_is_recovered(client, sql):
    """Simulates a crash: row left leased by a worker that no longer exists."""
    sql(
        """
        INSERT INTO transactions
            (transaction_id, source_account, destination_account, amount, currency,
             attempts, locked_until, created_at)
        VALUES ('txn_orphan', 'a', 'b', 10, 'USD', 1,
                now() - interval '1 minute', now() - interval '5 minutes')
        """
    )
    done = wait_for_status(client, "txn_orphan", "PROCESSED")
    # Delay was long past, so it should finish right away, not 1s later.
    assert done["processed_at"] is not None


def test_poison_row_ends_up_failed(client, sql):
    sql(
        """
        INSERT INTO transactions
            (transaction_id, source_account, destination_account, amount, currency,
             attempts, locked_until)
        VALUES ('txn_poison', 'a', 'b', 10, 'USD', 3, now() - interval '1 second')
        """
    )
    wait_for_status(client, "txn_poison", "FAILED")
