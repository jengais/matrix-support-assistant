"""Prompt files: one .md file per prompt, so prompts are reviewed like code."""

from functools import cache
from pathlib import Path

_DIR = Path(__file__).parent


@cache
def load_prompt(name: str) -> str:
    return (_DIR / f"{name}.md").read_text(encoding="utf-8").strip()


def wrap_customer_message(message: str) -> str:
    """Wrap untrusted customer text in tags; angle brackets are replaced so it cannot close the tag."""
    safe = message.replace("<", "‹").replace(">", "›")
    return load_prompt("customer_message").format(message=safe)
