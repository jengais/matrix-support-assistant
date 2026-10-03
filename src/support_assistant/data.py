"""Mock back-end: the order API, the policy store and the ticketing system.

Nothing is actually cancelled or refunded; a service request is only a record in a JSONL file.
"""

import asyncio
import json
import threading
import uuid
from datetime import UTC, datetime

from support_assistant import config
from support_assistant.models import Order, Policy, ServiceRequest, ServiceRequestType


def _load(name: str) -> list[dict]:
    return json.loads((config.DATA_DIR / name).read_text(encoding="utf-8"))


ORDERS = {o["order_id"]: Order(**o) for o in _load("orders.json")}
POLICIES = {p["id"]: Policy(**p) for p in _load("policies.json")}


class OrderServiceUnavailable(Exception):
    pass


async def get_order(order_id: str, customer_id: str, *, service_down: bool = False) -> Order | None:
    """Return the order only if it belongs to the logged-in customer (POL-01).

    An order of another customer is returned as None, exactly like a missing one,
    so the assistant can never reveal that it exists.
    """
    if service_down:
        raise OrderServiceUnavailable("order service is down (simulated)")
    order = ORDERS.get(order_id)
    return order if order and order.customer_id == customer_id else None


_file_lock = threading.Lock()  # the write runs in a worker thread; works with any event loop


async def open_service_request(
    customer_id: str, order_id: str | None, type: ServiceRequestType, summary: str
) -> ServiceRequest:
    request = ServiceRequest(
        request_id=f"SR-{uuid.uuid4().hex[:8].upper()}",
        type=type,
        customer_id=customer_id,
        order_id=order_id,
        summary=summary,
        created_at=datetime.now(UTC),
    )
    await asyncio.to_thread(_append, request)
    return request


def _append(request: ServiceRequest) -> None:
    path = config.SERVICE_REQUESTS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    with _file_lock, path.open("a", encoding="utf-8") as f:  # concurrent writes must not interleave
        f.write(request.model_dump_json() + "\n")
