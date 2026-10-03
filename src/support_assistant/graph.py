"""The LangGraph workflow.

    START ─┬─> guardrails ─────────────────────┐
           └─> understand ──> fetch_order ─────┴─> decide ─┬─> open_service_request ─┐
                                                           └─────────────────────────┴─> compose_reply
    compose_reply ─> finalize ─> END

- guardrails and understand are the two LLM calls on the raw message, so they run in parallel.
- fetch_order needs the order ids from understand, but not the guardrail verdict. That is safe:
  ownership is checked in code, and nothing reaches the customer before decide sees both branches.
- A message can hold several questions (several orders, or several questions about one order):
  each becomes one Query. Orders are fetched and service requests opened concurrently, rules are
  applied per question, and one reply covers them all.
- Every node has a fallback, so the graph always ends with a valid response.
"""

import asyncio
import logging
import operator
from dataclasses import dataclass, replace
from datetime import date
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from support_assistant import config, data, llm
from support_assistant.guardrails import detect_injection, find_order_ids, ground_order_id, validate_reply
from support_assistant.llm import GuardrailVerdict, OrderQuestion
from support_assistant.models import (
    Action,
    Intent,
    Order,
    OrderResult,
    ServiceRequest,
    ServiceRequestRef,
    SupportRequest,
    SupportResponse,
)
from support_assistant.policy import TEMPLATE_ONLY, TEMPLATES, Decision, Lookup, decide, render_template

logger = logging.getLogger(__name__)

MAX_ORDERS_PER_MESSAGE = 5


@dataclass(frozen=True)
class Query:
    """One question of the customer, about one order. Each node returns updated copies."""

    question: OrderQuestion | None  # None when the model call failed
    order_id: str | None  # the order number as asked, grounded in the message
    lookup: Lookup = "skipped"
    order: Order | None = None  # set only if the order belongs to this customer
    decision: Decision | None = None
    service_request: ServiceRequest | None = None


class State(TypedDict, total=False):
    customer_id: str  # trusted, from the login session
    message: str
    guardrail: GuardrailVerdict
    queries: list[Query]
    reply: str
    response: SupportResponse
    errors: Annotated[list[str], operator.add]  # written by parallel nodes, so lists are merged


@dataclass
class Context:
    """Per-run settings passed to the nodes."""

    today: date
    order_service_down: bool = False  # simulates an outage (test case T11)


# --- Nodes --------------------------------------------------------------------------------------


async def guardrails(state: State) -> dict:
    message = state["message"]
    if match := detect_injection(message):  # cheap check first: no LLM call needed
        verdict = GuardrailVerdict(is_relevant=True, is_prompt_injection=True, rationale=f"matched {match!r}")
        return {"guardrail": verdict}
    try:
        return {"guardrail": await llm.check_guardrails(message)}
    except Exception as exc:  # API error, timeout or invalid output
        # Let it through: ownership is still enforced in code, so nothing can leak.
        verdict = GuardrailVerdict(
            is_relevant=True, is_prompt_injection=False, rationale="guardrail LLM failed"
        )
        return {"guardrail": verdict, "errors": [f"guardrails: {exc}"[:200]]}


async def understand(state: State) -> dict:
    message = state["message"]
    try:
        analysis = await llm.analyze(message)
    except Exception as exc:
        # The order ids can still be found with a regex, so the agent gets them with the handoff.
        queries = [Query(None, order_id) for order_id in find_order_ids(message)] or [Query(None, None)]
        return {"queries": queries[:MAX_ORDERS_PER_MESSAGE], "errors": [f"understand: {exc}"[:200]]}

    queries, seen = [], set()
    for question in analysis.questions:
        order_id = ground_order_id(question.order_id, message)  # drops ids the model invented
        # One entry per (order, intent): two different questions about the same order are both kept.
        # Without an order number one clarifying question is enough, whatever the intent.
        key = (order_id, _effective_intent(question)) if order_id else None
        if key not in seen:
            seen.add(key)
            queries.append(Query(question, order_id))
    if not queries:  # the model returned nothing usable: treat as a request for an agent
        other = OrderQuestion(intent=Intent.OTHER, order_id=None, product_opened=None, product_damaged=False)
        queries = [Query(other, None)]
    return {"queries": queries[:MAX_ORDERS_PER_MESSAGE]}


def _effective_intent(question: OrderQuestion) -> Intent:
    return Intent.DAMAGED_PRODUCT if question.product_damaged else question.intent


async def fetch_order(state: State, runtime: Runtime[Context]) -> dict:
    async def lookup(order_id: str) -> tuple[Lookup, Order | None, str | None]:
        try:
            order = await data.get_order(
                order_id, state["customer_id"], service_down=runtime.context.order_service_down
            )
        except data.OrderServiceUnavailable as exc:
            return "unavailable", None, f"fetch_order {order_id}: {exc}"
        return ("found" if order else "not_found"), order, None

    # Each distinct order is fetched once, all of them at the same time.
    order_ids = list(dict.fromkeys(q.order_id for q in state["queries"] if q.order_id))
    found = dict(zip(order_ids, await asyncio.gather(*(lookup(i) for i in order_ids)), strict=True))

    queries = [
        replace(q, lookup=found[q.order_id][0], order=found[q.order_id][1])
        if q.order_id
        else replace(q, lookup="no_order_id")
        for q in state["queries"]
    ]
    return {"queries": queries, "errors": [error for _, _, error in found.values() if error]}


async def decide_node(state: State, runtime: Runtime[Context]) -> dict:
    guardrail, queries = state["guardrail"], state["queries"]
    if guardrail.is_prompt_injection or not guardrail.is_relevant:
        queries = queries[:1]  # a verdict about the whole message gets one answer
    decided = [
        replace(q, decision=decide(guardrail, q.question, q.lookup, q.order, runtime.context.today))
        for q in queries
    ]
    return {"queries": decided}


async def open_service_request(state: State) -> dict:
    async def open_one(query: Query) -> tuple[Query, str | None]:
        decision = query.decision
        if not decision.service_request:
            return query, None
        try:
            request = await data.open_service_request(
                state["customer_id"], decision.order_id, decision.service_request, decision.reason
            )
        except OSError as exc:
            failed = Decision(
                decision.intent,
                Action.ESCALATE,
                "system_error",
                decision.source_ids,
                f"{decision.reason} | Could not open the service request.",
                decision.order_id,
            )
            return replace(query, decision=failed), f"open_service_request: {exc}"
        return replace(query, service_request=request), None

    results = await asyncio.gather(*(open_one(q) for q in state["queries"]))  # all requests at once
    return {"queries": [q for q, _ in results], "errors": [e for _, e in results if e]}


async def compose_reply(state: State, runtime: Runtime[Context]) -> dict:
    queries = state["queries"]
    request_ids = [q.service_request.request_id for q in queries if q.service_request]
    draft = "\n".join(
        render_template(
            q.decision.template,
            q.order,
            runtime.context.today,
            q.service_request.request_id if q.service_request else None,
            q.order_id,
        )
        for q in queries
    )
    if any(q.decision.template in TEMPLATE_ONLY or q.question is None for q in queries):
        return {"reply": draft}

    source_ids = sorted({s for q in queries for s in q.decision.source_ids})
    try:
        reply = await llm.compose_reply(
            state["message"],
            decisions=[
                {
                    "order_id": q.order_id,
                    "action": q.decision.action,
                    "intent": q.decision.intent,
                    "service_request_id": q.service_request.request_id if q.service_request else None,
                }
                for q in queries
            ],
            policies=[data.POLICIES[p].model_dump() for p in source_ids],
            draft=draft,
        )
    except Exception as exc:
        return {"reply": draft, "errors": [f"compose_reply: {exc}"[:200]]}

    asked_ids = {q.order_id for q in queries if q.order_id}
    problems = validate_reply(reply, order_ids=asked_ids, request_ids=request_ids)
    if problems:  # the model broke a rule: send the approved draft instead
        return {"reply": draft, "errors": [f"reply rejected: {'; '.join(problems)}"]}
    return {"reply": reply}


_PRIORITY = {Action.REPLY: 0, Action.CLARIFY: 1, Action.ESCALATE: 2}


async def finalize(state: State) -> dict:
    results = [_to_result(q) for q in state["queries"]]
    errors = state.get("errors", [])
    degraded = f" | Degraded: {'; '.join(errors)}" if errors else ""
    try:
        if len(results) == 1:  # the common case: the flat contract from the assignment
            single = results[0]
            response = SupportResponse(
                **single.model_dump(exclude={"reason"}), reply=state["reply"], reason=single.reason + degraded
            )
        else:
            main = max(results, key=lambda r: _PRIORITY[r.action])
            order_ids = {r.order_id for r in results}
            requests = [r.service_request for r in results if r.service_request]
            response = SupportResponse(
                intent=main.intent,
                action=main.action,
                reply=state["reply"],
                order_id=order_ids.pop() if len(order_ids) == 1 else None,  # one order, several questions
                source_ids=sorted({s for r in results for s in r.source_ids}),
                service_request=requests[0] if len(requests) == 1 else None,
                reason=f"Message has {len(results)} questions, see items.{degraded}",
                items=results,
            )
    except ValueError as exc:  # pydantic.ValidationError is a ValueError
        response = fallback_response(f"Output validation failed: {exc}")
    return {"response": response}


def _to_result(query: Query) -> OrderResult:
    decision, request = query.decision, query.service_request
    return OrderResult(
        intent=decision.intent,
        action=decision.action,
        order_id=decision.order_id,
        source_ids=decision.source_ids,
        service_request=ServiceRequestRef(type=request.type, request_id=request.request_id)
        if request
        else None,
        reason=decision.reason,
    )


def fallback_response(reason: str) -> SupportResponse:
    return SupportResponse(
        intent=Intent.OTHER,
        action=Action.ESCALATE,
        reply=TEMPLATES["system_error"],
        order_id=None,
        source_ids=["POL-07"],
        service_request=None,
        reason=reason,
    )


# --- Graph ----------------------------------------------------------------------------------------


def _needs_service_request(state: State) -> str:
    if any(q.decision.service_request for q in state["queries"]):
        return "open_service_request"
    return "compose_reply"


def build_graph():
    builder = StateGraph(State, context_schema=Context)
    builder.add_node("guardrails", guardrails)
    builder.add_node("understand", understand)
    builder.add_node("fetch_order", fetch_order)
    builder.add_node("decide", decide_node)
    builder.add_node("open_service_request", open_service_request)
    builder.add_node("compose_reply", compose_reply)
    builder.add_node("finalize", finalize)

    builder.add_edge(START, "guardrails")
    builder.add_edge(START, "understand")  # runs in parallel with guardrails
    builder.add_edge("understand", "fetch_order")
    builder.add_edge(["guardrails", "fetch_order"], "decide")  # waits for both branches
    builder.add_conditional_edges("decide", _needs_service_request, ["open_service_request", "compose_reply"])
    builder.add_edge("open_service_request", "compose_reply")
    builder.add_edge("compose_reply", "finalize")
    builder.add_edge("finalize", END)
    return builder.compile()


graph = build_graph()


async def run(
    request: SupportRequest,
    *,
    today: date | None = None,
    order_service_down: bool = False,
    trace_name: str = "support_request",
) -> SupportResponse:
    context = Context(today=today or config.today(), order_service_down=order_service_down)
    try:
        state = await graph.ainvoke(
            {"customer_id": request.customer_id, "message": request.message, "errors": []},
            # One LangSmith trace per request, named and searchable by metadata.
            config={"run_name": trace_name, "metadata": {"customer_id": request.customer_id}},
            context=context,
        )
        return state["response"]
    except Exception as exc:  # a bug must never reach the customer as a crash
        logger.exception("workflow failed")
        return fallback_response(f"Unhandled error: {type(exc).__name__}")


_limit = asyncio.Semaphore(8)  # max concurrent runs, to stay within the OpenAI rate limits


async def run_many(requests: list[SupportRequest]) -> list[SupportResponse]:
    async def limited(request: SupportRequest) -> SupportResponse:
        async with _limit:
            return await run(request)

    return await asyncio.gather(*(limited(r) for r in requests))
