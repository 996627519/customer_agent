"""Minimal order repository boundary; production deployments can replace it with the order API."""

from typing import Protocol, Any


class OrderService(Protocol):
    def get_order_for_user(self, *, user_id: str, order_id: str) -> dict[str, Any] | None: ...


class MySqlOrderService:
    def __init__(self, database):
        self.database = database

    def get_order_for_user(self, *, user_id: str, order_id: str):
        return self.database.get_order_for_user(user_id, order_id)
