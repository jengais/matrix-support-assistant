"""FastAPI backend. Run with: uv run support-assistant serve"""

from fastapi import FastAPI, HTTPException

from support_assistant.graph import run, run_many
from support_assistant.models import SupportRequest, SupportResponse

app = FastAPI(title="Support Assistant")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/support")
async def support(request: SupportRequest) -> SupportResponse:
    return await run(request)


@app.post("/support/batch")
async def support_batch(requests: list[SupportRequest]) -> list[SupportResponse]:
    """Answers several messages concurrently; results keep the input order."""
    if not 1 <= len(requests) <= 50:
        raise HTTPException(422, "send between 1 and 50 messages")
    return await run_many(requests)
