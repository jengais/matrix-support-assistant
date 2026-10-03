"""Deterministic checks around the model: injection screen, order-id grounding, reply validation."""

import re

# First-line injection screen. If it matches, the LLM guardrail call is skipped.
_INJECTION = re.compile(
    r"ignore (all |any )?(previous |prior )?(instructions|rules|checks)|system prompt|you are now"
    r"|\b(ת?התעלם|תתעלמי)\b|\b(עקוף|תעקוף|לעקוף)\b|\bאני\s+ה?(מנהל|מנהלת|אדמין|מפתח)\b",
    re.IGNORECASE,
)


def detect_injection(message: str) -> str | None:
    match = _INJECTION.search(message)
    return match.group(0) if match else None


_ORDER_ID = re.compile(r"\bORD[-\s]?(\d{4})\b", re.IGNORECASE)


def ground_order_id(model_order_id: str | None, message: str) -> str | None:
    """Accept the model's order id only if its digits appear in the message (no invented ids)."""
    digits = re.sub(r"\D", "", model_order_id or "")
    return f"ORD-{digits}" if len(digits) == 4 and digits in message else None


def find_order_ids(message: str) -> list[str]:
    """Regex fallback used when the model call fails."""
    return list(dict.fromkeys(f"ORD-{m}" for m in _ORDER_ID.findall(message)))


# Claims the reply must never make (POL-04: a request is not a cancellation; POL-06/07: no refunds).
_FORBIDDEN = re.compile(
    r"(?<!\w)ההזמנה(\s+(?!לא\b|טרם\b)\S+)?\s+בוטלה"  # skips "...שההזמנה בוטלה" and negations
    r"|ההחזר(\s+הכספי)?\s+אושר|has been cancell?ed|refund (has been|was) (issued|approved)",
    re.IGNORECASE,
)


def validate_reply(reply: str, *, order_ids: set[str], request_ids: list[str]) -> list[str]:
    """Return the problems found in a model-written reply; an empty list means it is safe to send."""
    problems = []
    if not reply.strip():
        problems.append("empty reply")
    other_orders = {m.upper() for m in re.findall(r"ORD-\d+", reply, re.IGNORECASE)} - order_ids
    if other_orders:
        problems.append(f"mentions other orders {sorted(other_orders)}")
    missing = [r for r in request_ids if r not in reply]
    if missing:
        problems.append(f"missing request ids {missing}")
    if match := _FORBIDDEN.search(reply):
        problems.append(f"forbidden claim {match.group(0)!r}")
    return problems
