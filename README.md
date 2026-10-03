# Support Assistant

An AI customer-service assistant prototype for an e-commerce site: it decides whether to reply, ask a clarifying question or escalate to a human agent.

The plan, the pipeline, the design decisions, the test results and the improvements are all in [SUBMISSION.md](SUBMISSION.md).

## Requirements

- [uv](https://docs.astral.sh/uv/) (Python package and project manager). It downloads Python 3.12 if it's missing.
- An OpenAI API key.
- Optional: a LangSmith API key, for tracing.

## Installation

1. Install uv, if you don't have it:

   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

2. From the project root, install the dependencies:

   ```bash
   uv sync
   ```

3. Create your `.env` file from the template and fill in the keys:

   ```bash
   cp .env.example .env
   ```

   | Variable | Required | Purpose |
   |---|---|---|
   | `OPENAI_API_KEY` | yes | OpenAI API key |
   | `OPENAI_MODEL` | no | Model name (default `gpt-6-luna`) |
   | `LANGSMITH_TRACING` | no | `true` to send traces to LangSmith |
   | `LANGSMITH_API_KEY` | no | LangSmith API key |
   | `LANGSMITH_PROJECT` | no | LangSmith project name |
   | `REFERENCE_DATE` | no | Business "today"; the assignment uses `2026-09-22` |
   | `DATA_DIR` | no | Folder with the policy and order data (default `data`) |
   | `SERVICE_REQUESTS_FILE` | no | Where service requests are written (default `var/service_requests.jsonl`) |

## Run

```bash
uv run support-assistant serve                     # API on http://127.0.0.1:8000 (interactive docs at /docs)
uv run support-assistant ask "אני רוצה לבטל את הזמנה ORD-1001."
uv run support-assistant eval                      # runs all test cases → results/eval_report.md
uv run pytest                                      # offline tests with a fake model, no API key needed
```

Example request to the running API:

```bash
curl -s localhost:8000/support -H 'content-type: application/json' \
  -d '{"customer_id":"C-101","message":"איפה הזמנה ORD-1006 ומתי היא תגיע?"}'
```
