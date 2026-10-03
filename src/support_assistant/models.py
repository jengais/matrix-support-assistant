"""Data models: orders, policies, service requests and the assistant's input/output contract."""

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class OrderStatus(StrEnum):
    PROCESSING = "processing"
    SHIPPED = "shipped"
    DELIVERED = "delivered"


class Intent(StrEnum):
    ORDER_STATUS = "order_status"  # where is my order / it is late
    CANCEL_ORDER = "cancel_order"
    RETURN_ORDER = "return_order"
    DAMAGED_PRODUCT = "damaged_product"
    OTHER = "other"  # store-related, but not covered by the automated policies
    OUT_OF_SCOPE = "out_of_scope"


class Action(StrEnum):
    REPLY = "reply"
    CLARIFY = "clarify"
    ESCALATE = "escalate"


class ServiceRequestType(StrEnum):
    SHIPPING_INQUIRY = "shipping_inquiry"
    CANCELLATION = "cancellation"
    RETURN = "return"
    AGENT_HANDOFF = "agent_handoff"


class Order(BaseModel):
    order_id: str
    customer_id: str
    status: OrderStatus
    estimated_delivery: date
    delivered_at: date | None


class Policy(BaseModel):
    id: str
    topic: str
    policy: str


class ServiceRequest(BaseModel):
    request_id: str
    type: ServiceRequestType
    customer_id: str
    order_id: str | None
    summary: str
    created_at: datetime


# --- API contract ---------------------------------------------------------------------------------


class SupportRequest(BaseModel):
    customer_id: str = Field(pattern=r"^C-\d+$", description="Trusted id from the login session.")
    message: str = Field(min_length=1, max_length=2000)


class ServiceRequestRef(BaseModel):
    type: ServiceRequestType
    request_id: str


class OrderResult(BaseModel):
    """The decision for one question, when a message holds several."""

    intent: Intent
    action: Action
    order_id: str | None
    source_ids: list[str]
    service_request: ServiceRequestRef | None
    reason: str


class SupportResponse(BaseModel):
    intent: Intent
    action: Action
    reply: str = Field(min_length=1)
    order_id: str | None = Field(pattern=r"^ORD-\d+$")
    source_ids: list[str]
    service_request: ServiceRequestRef | None
    reason: str = Field(min_length=1, description="Internal note for the service team.")
    items: list[OrderResult] = Field(
        default=[],
        description="One entry per question when the message holds several (several orders, or several "
        "questions about one order). Then action is the most important one (escalate > clarify > reply), "
        "order_id is set only if all questions are about the same order, and service_request only if "
        "exactly one request was opened.",
    )

    @model_validator(mode="after")
    def _check(self) -> "SupportResponse":
        for result in [self, *self.items]:
            if result.action is Action.CLARIFY and result.service_request:
                raise ValueError("a clarifying question must not open a service request")
        return self
