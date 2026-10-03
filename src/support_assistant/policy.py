"""Business rules (POL-01..07) as plain code, plus the approved reply template for each outcome.

The model never decides eligibility. It only classifies the message and rephrases the template,
so a persuasive customer cannot talk the assistant out of a rule.
"""

from dataclasses import dataclass
from datetime import date
from typing import Literal

from support_assistant.llm import GuardrailVerdict, OrderQuestion
from support_assistant.models import Action, Intent, Order, OrderStatus
from support_assistant.models import ServiceRequestType as SR

RETURN_WINDOW_DAYS = 14

Lookup = Literal["found", "not_found", "no_order_id", "unavailable", "skipped"]


@dataclass(frozen=True)
class Decision:
    intent: Intent
    action: Action
    template: str  # key in TEMPLATES
    source_ids: list[str]
    reason: str  # for the service team
    order_id: str | None = None  # set only for a verified order of this customer
    service_request: SR | None = None


# Approved replies. They are the draft the model rephrases, and the fallback sent as-is when the
# model fails or its reply does not pass validation.
TEMPLATES = {
    "status": "הזמנה {order_id} נמצאת בסטטוס: {status}. מועד האספקה המשוער הוא {eta}. "
    "שימו לב שזהו מועד משוער ולא התחייבות.",
    "status_delivered": "לפי המערכת, הזמנה {order_id} נמסרה בתאריך {delivered_at}.",
    "late_inquiry": "הזמנה {order_id} נמצאת בסטטוס: {status}, ומועד האספקה המשוער ({eta}) כבר עבר. "
    "פתחנו עבורכם בקשת בירור משלוח שמספרה {request_id}, וצוות השירות יבדוק את הנושא. "
    "בשלב זה איננו יכולים לציין מועד אספקה חדש.",
    "cancel_requested": "פתחנו בקשת ביטול להזמנה {order_id} (מספר בקשה {request_id}). "
    "שימו לב: פתיחת הבקשה אינה אישור שההזמנה בוטלה. נעדכן אתכם לאחר שהבקשה תטופל.",
    "cancel_shipped": "הזמנה {order_id} כבר נשלחה, ולכן לא ניתן לפתוח עבורה בקשת ביטול. "
    "לאחר שההזמנה תימסר, ניתן יהיה לבחון החזרה בהתאם למדיניות ההחזרות.",
    "cancel_delivered": "הזמנה {order_id} כבר נמסרה, ולכן לא ניתן לבטל אותה. ניתן לבקש החזרה "
    "עד 14 ימים ממועד המסירה, בתנאי שהמוצר לא נפתח.",
    "return_requested": "פתחנו בקשת החזרה להזמנה {order_id} (מספר בקשה {request_id}). "
    "צוות השירות יחזור אליכם עם המשך הטיפול.",
    "return_not_delivered": "הזמנה {order_id} עדיין לא נמסרה. ניתן לפתוח בקשת החזרה רק לאחר המסירה, "
    "עד 14 ימים ממועד המסירה ובתנאי שהמוצר לא נפתח.",
    "return_ask_condition": "כדי לבדוק את האפשרות להחזיר את הזמנה {order_id}, נשמח לדעת: "
    "האם המוצר נפתח, או שהוא עדיין סגור באריזתו המקורית?",
    "return_out_of_window": "הזמנה {order_id} נמסרה בתאריך {delivered_at}, לפני {days} ימים, "
    "כלומר מעבר לחלון ההחזרה של 14 ימים. העברנו את הבקשה לבדיקת נציג שירות "
    "(מספר בקשה {request_id}), ואיננו יכולים להבטיח שההחזרה תאושר.",
    "return_opened": "מאחר שהמוצר בהזמנה {order_id} נפתח, הבקשה אינה עומדת בתנאי ההחזרה. "
    "העברנו אותה לבדיקת נציג שירות (מספר בקשה {request_id}), ואיננו יכולים להבטיח שתאושר.",
    "damaged": "אנו מצטערים לשמוע שהמוצר בהזמנה {order_id} הגיע פגום. העברנו את הדיווח לנציג שירות "
    "(מספר בקשה {request_id}) שיחזור אליכם. בשלב זה איננו יכולים להבטיח החלפה או החזר כספי.",
    "ask_order_id": "כדי שנוכל לעזור, מה מספר ההזמנה?",
    "order_not_found": "לא מצאנו את הזמנה {requested_id} בחשבון שלכם. "
    "אפשר לבדוק את מספר ההזמנה ולשלוח אותו שוב?",
    "injection_refusal": "נוכל לסייע רק בפניות הנוגעות להזמנות שבחשבון שלכם, ולא ניתן לעקוף את "
    "בדיקות ההרשאה. אם יש לכם שאלה על אחת מההזמנות שלכם, נשמח לעזור.",
    "out_of_scope": "נוכל לעזור בנושאים הקשורים להזמנות שלכם: סטטוס משלוח, ביטולים, החזרות "
    "ודיווח על מוצר פגום. במה נוכל לעזור?",
    "handoff": "העברנו את הפנייה לנציג שירות (מספר בקשה {request_id}) שיחזור אליכם בהקדם.",
    "service_unavailable": "מערכת ההזמנות אינה זמינה כרגע, ולכן לא נוכל לבדוק את פרטי ההזמנה. "
    "העברנו את הפנייה לנציג שירות (מספר בקשה {request_id}) שיחזור אליכם.",
    "system_error": "אירעה תקלה זמנית ולא הצלחנו להשלים את הבקשה. אנא נסו שוב בעוד מספר דקות.",
}

# Sent verbatim, without the model: the customer text is hostile/irrelevant, or the system failed.
TEMPLATE_ONLY = {"injection_refusal", "out_of_scope", "system_error"}

STATUS_HE = {
    OrderStatus.PROCESSING: "בטיפול (טרם נשלחה)",
    OrderStatus.SHIPPED: "נשלחה",
    OrderStatus.DELIVERED: "נמסרה",
}


def decide(
    guardrail: GuardrailVerdict,
    question: OrderQuestion | None,  # None when the model call failed
    lookup: Lookup,
    order: Order | None,
    today: date,
) -> Decision:
    if guardrail.is_prompt_injection:
        return Decision(
            question.intent if question else Intent.OTHER,
            Action.REPLY,
            "injection_refusal",
            ["POL-01"],
            f"Manipulation attempt: {guardrail.rationale} No order data disclosed.",
        )

    if not guardrail.is_relevant or (question and question.intent is Intent.OUT_OF_SCOPE):
        return Decision(Intent.OUT_OF_SCOPE, Action.REPLY, "out_of_scope", [], "Out of scope.")

    if question is None or question.intent is Intent.OTHER:
        why = "Message analysis failed (LLM error)" if question is None else "Request not covered by policy"
        return Decision(
            Intent.OTHER,
            Action.ESCALATE,
            "handoff",
            ["POL-07"],
            f"{why}; handed off to an agent.",
            order.order_id if order else None,
            SR.AGENT_HANDOFF,
        )

    intent = Intent.DAMAGED_PRODUCT if question.product_damaged else question.intent

    if lookup == "no_order_id":
        return Decision(intent, Action.CLARIFY, "ask_order_id", ["POL-07"], "No order number; asking.")
    if lookup == "unavailable":
        return Decision(
            intent,
            Action.ESCALATE,
            "service_unavailable",
            ["POL-07"],
            "Order service unavailable; cannot verify the order. Handed off to an agent.",
            service_request=SR.AGENT_HANDOFF,
        )
    if lookup == "not_found":
        return Decision(
            intent,
            Action.CLARIFY,
            "order_not_found",
            ["POL-01", "POL-07"],
            "Order not found for this customer (missing or another customer's; not distinguished).",
        )

    return _decide_for_order(intent, question.product_opened, order, today)


def _decide_for_order(intent: Intent, product_opened: bool | None, order: Order, today: date) -> Decision:
    def result(action, template, sources, reason, service_request=None) -> Decision:
        context = f"{order.order_id} [{order.status}, ETA {order.estimated_delivery}, today {today}]"
        return Decision(
            intent, action, template, sources, f"{context}: {reason}", order.order_id, service_request
        )

    if intent is Intent.DAMAGED_PRODUCT:
        return result(
            Action.ESCALATE, "damaged", ["POL-06"], "Damaged product; no refund promised.", SR.AGENT_HANDOFF
        )

    if intent is Intent.ORDER_STATUS:
        if order.status is not OrderStatus.DELIVERED and order.estimated_delivery < today:
            return result(
                Action.REPLY,
                "late_inquiry",
                ["POL-02", "POL-03"],
                "Estimated date passed, not delivered; shipping inquiry opened.",
                SR.SHIPPING_INQUIRY,
            )
        if order.status is OrderStatus.DELIVERED:
            return result(Action.REPLY, "status_delivered", ["POL-02"], "Delivery date shown.")
        return result(Action.REPLY, "status", ["POL-02"], "Status and estimated date shown.")

    if intent is Intent.CANCEL_ORDER:
        if order.status is OrderStatus.PROCESSING:
            return result(
                Action.REPLY,
                "cancel_requested",
                ["POL-04"],
                "Processing; cancellation request opened (not a confirmed cancellation).",
                SR.CANCELLATION,
            )
        if order.status is OrderStatus.SHIPPED:
            return result(Action.REPLY, "cancel_shipped", ["POL-04"], "Shipped; cannot cancel.")
        return result(Action.REPLY, "cancel_delivered", ["POL-04", "POL-05"], "Delivered; cannot cancel.")

    # Intent.RETURN_ORDER
    if order.delivered_at is None:
        return result(Action.REPLY, "return_not_delivered", ["POL-05"], "Not delivered yet.")
    days = (today - order.delivered_at).days
    if days > RETURN_WINDOW_DAYS:  # day 14 itself is still allowed
        return result(
            Action.ESCALATE,
            "return_out_of_window",
            ["POL-05"],
            f"Delivered {days} days ago (> 14); escalated, approval not promised.",
            SR.AGENT_HANDOFF,
        )
    if product_opened is None:
        return result(
            Action.CLARIFY, "return_ask_condition", ["POL-05"], f"Day {days}; product condition unknown."
        )
    if product_opened:
        return result(
            Action.ESCALATE,
            "return_opened",
            ["POL-05"],
            "Product opened; escalated, approval not promised.",
            SR.AGENT_HANDOFF,
        )
    return result(
        Action.REPLY, "return_requested", ["POL-05"], f"Day {days}, unopened; return opened.", SR.RETURN
    )


def render_template(
    template: str, order: Order | None, today: date, request_id: str | None, requested_id: str | None
) -> str:
    """Fill a template with facts from the system only (order data, request id), never from the model."""
    facts = {"request_id": request_id or "", "requested_id": requested_id or ""}
    if order:
        facts |= {
            "order_id": order.order_id,
            "status": STATUS_HE[order.status],
            "eta": order.estimated_delivery.strftime("%d.%m.%Y"),
            "delivered_at": order.delivered_at.strftime("%d.%m.%Y") if order.delivered_at else "",
            "days": str((today - order.delivered_at).days) if order.delivered_at else "",
        }
    return TEMPLATES[template].format(**facts)
