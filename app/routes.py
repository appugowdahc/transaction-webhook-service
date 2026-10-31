import asyncio
import logging
from datetime import datetime, timezone

import asyncpg
from fastapi import APIRouter, HTTPException, Request, status

from app import repository
from app.models import TransactionOut, TransactionWebhook

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/")
async def health():
    now = datetime.now(timezone.utc)
    return {"status": "HEALTHY", "current_time": now.strftime("%Y-%m-%dT%H:%M:%SZ")}


@router.post("/v1/webhooks/transactions", status_code=status.HTTP_202_ACCEPTED)
async def receive_transaction(txn: TransactionWebhook, request: Request):
    try:
        created = await repository.insert_if_absent(request.app.state.pool, txn)
    except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, asyncio.TimeoutError):
        # Not stored, so don't ACK. 5xx makes the sender retry.
        log.exception("could not persist webhook %s", txn.transaction_id)
        raise HTTPException(status_code=503, detail="temporarily unavailable")

    if not created:
        log.info("duplicate webhook for %s ignored", txn.transaction_id)
    # Same answer for new and duplicate deliveries.
    return {"status": "accepted", "transaction_id": txn.transaction_id}


@router.get("/v1/transactions/{transaction_id}", response_model=list[TransactionOut])
async def get_transaction(transaction_id: str, request: Request):
    row = await repository.get(request.app.state.pool, transaction_id)
    if row is None:
        raise HTTPException(status_code=404, detail="transaction not found")
    # List-shaped to match the contract in the assignment.
    return [dict(row)]
