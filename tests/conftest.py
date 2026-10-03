from datetime import date

import pytest

from support_assistant import config, llm
from support_assistant.llm import GuardrailVerdict, MessageAnalysis, OrderQuestion

TODAY = date(2026, 9, 22)


class FakeLLM:
    """Replaces the three OpenAI calls. compose_reply returns the draft unless `reply` is set."""

    def __init__(self):
        self.analyses: dict[str, MessageAnalysis] = {}
        self.injection = False
        self.reply: str | None = None
        self.fail = False
        self.calls: list[str] = []

    async def check_guardrails(self, message):
        self.calls.append("guardrails")
        return GuardrailVerdict(is_relevant=True, is_prompt_injection=self.injection, rationale="test")

    async def analyze(self, message):
        self.calls.append("analyze")
        if self.fail:
            raise RuntimeError("model down")
        return self.analyses[message]

    async def compose_reply(self, message, decisions, policies, draft):
        self.calls.append("compose")
        return self.reply if self.reply is not None else draft


@pytest.fixture
def fake_llm(monkeypatch, tmp_path) -> FakeLLM:
    fake = FakeLLM()
    for name in ("check_guardrails", "analyze", "compose_reply"):
        monkeypatch.setattr(llm, name, getattr(fake, name))
    monkeypatch.setattr(config, "SERVICE_REQUESTS_FILE", tmp_path / "requests.jsonl")
    return fake


def question(intent, order_id=None, *, opened=None, damaged=False) -> OrderQuestion:
    return OrderQuestion(intent=intent, order_id=order_id, product_opened=opened, product_damaged=damaged)


def analysis(*questions: OrderQuestion) -> MessageAnalysis:
    return MessageAnalysis(questions=list(questions))
