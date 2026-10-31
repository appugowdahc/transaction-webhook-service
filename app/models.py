from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_serializer


class TransactionWebhook(BaseModel):
    transaction_id: str = Field(min_length=1, max_length=128)
    source_account: str = Field(min_length=1, max_length=128)
    destination_account: str = Field(min_length=1, max_length=128)
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    currency: str = Field(pattern=r"^[A-Z]{3}$")


class TransactionOut(BaseModel):
    transaction_id: str
    source_account: str
    destination_account: str
    amount: Decimal
    currency: str
    status: str
    created_at: datetime
    processed_at: datetime | None

    # Stored as NUMERIC so nothing is lost in the DB, but the API contract
    # shows amount as a JSON number.
    @field_serializer("amount")
    def _amount_as_number(self, v: Decimal) -> float:
        return float(v)
