"""Settings, read from environment variables loaded from the .env file.

load_dotenv() also exposes OPENAI_API_KEY and LANGSMITH_* to the OpenAI and LangSmith SDKs,
which read them directly from the environment.
"""

import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-6-luna")
DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
SERVICE_REQUESTS_FILE = Path(os.getenv("SERVICE_REQUESTS_FILE", "var/service_requests.jsonl"))
REFERENCE_DATE = os.getenv("REFERENCE_DATE")  # the assignment fixes "today" to 2026-09-22


def today() -> date:
    return date.fromisoformat(REFERENCE_DATE) if REFERENCE_DATE else date.today()
