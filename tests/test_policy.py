"""Business rules, tested without any model."""

from datetime import timedelta

import pytest

from support_assistant.data import ORDERS
from support_assistant.llm import GuardrailVerdict
from support_assistant.models import Action
from support_assistant.models import ServiceRequestType as SR
from support_assistant.policy import TEMPLATES, decide, render_template

from .conftest import TODAY, question

SAFE = GuardrailVerdict(is_relevant=True, is_prompt_injection=False, rationale="")


@pytest.mark.parametrize(
    "case, intent, order_id, opened, action, ticket, sources",
    [
        ("T01", "order_status", "ORD-1006", None, Action.REPLY, None, ["POL-02"]),
        ("T02", "order_status", "ORD-1002", None, Action.REPLY, SR.SHIPPING_INQUIRY, ["POL-02", "POL-03"]),
        ("T03", "cancel_order", "ORD-1001", None, Action.REPLY, SR.CANCELLATION, ["POL-04"]),
        ("T04", "cancel_order", "ORD-1006", None, Action.REPLY, None, ["POL-04"]),
        ("T05", "return_order", "ORD-1003", None, Action.CLARIFY, None, ["POL-05"]),
        ("T06", "return_order", "ORD-1004", False, Action.ESCALATE, SR.AGENT_HANDOFF, ["POL-05"]),
        ("T10", "return_order", "ORD-1003", False, Action.REPLY, SR.RETURN, ["POL-05"]),
        ("opened", "return_order", "ORD-1003", True, Action.ESCALATE, SR.AGENT_HANDOFF, ["POL-05"]),
        ("T07", "damaged_product", "ORD-1003", None, Action.ESCALATE, SR.AGENT_HANDOFF, ["POL-06"]),
    ],
)
def test_rules(case, intent, order_id, opened, action, ticket, sources):
    d = decide(SAFE, question(intent, order_id, opened=opened), "found", ORDERS[order_id], TODAY)
    assert (d.action, d.service_request, d.source_ids, d.order_id) == (action, ticket, sources, order_id)


@pytest.mark.parametrize("days, action", [(14, Action.REPLY), (15, Action.ESCALATE)])
def test_return_window_includes_day_14(days, action):
    order = ORDERS["ORD-1003"]
    d = decide(
        SAFE, question("return_order", opened=False), "found", order, order.delivered_at + timedelta(days)
    )
    assert d.action is action


def test_injection_is_refused_without_order_data():
    guard = GuardrailVerdict(is_relevant=True, is_prompt_injection=True, rationale="admin claim")
    d = decide(guard, question("order_status", "ORD-1001"), "found", ORDERS["ORD-1001"], TODAY)
    assert (d.action, d.order_id, d.service_request, d.source_ids) == (Action.REPLY, None, None, ["POL-01"])


@pytest.mark.parametrize(
    "lookup, action, ticket",
    [
        ("no_order_id", Action.CLARIFY, None),  # T09
        ("not_found", Action.CLARIFY, None),  # another customer's order
        ("unavailable", Action.ESCALATE, SR.AGENT_HANDOFF),  # T11
    ],
)
def test_missing_or_unavailable_order(lookup, action, ticket):
    d = decide(SAFE, question("order_status"), lookup, None, TODAY)
    assert (d.action, d.service_request, d.order_id) == (action, ticket, None)


@pytest.mark.parametrize("template", list(TEMPLATES))
def test_every_template_renders(template):
    assert render_template(template, ORDERS["ORD-1003"], TODAY, "SR-ABCDEF12", "ORD-1003")
