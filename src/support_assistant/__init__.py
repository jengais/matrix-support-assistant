"""Command line: `support-assistant serve | ask "<message>" | eval`."""

import argparse
import asyncio
import logging


def main() -> None:
    parser = argparse.ArgumentParser(prog="support-assistant")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve", help="run the FastAPI server on http://127.0.0.1:8000")
    ask = sub.add_parser("ask", help="send one message through the workflow")
    ask.add_argument("message")
    ask.add_argument("--customer", default="C-101")
    sub.add_parser("eval", help="run and grade data/test_cases.json")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if args.command == "serve":
        import uvicorn

        uvicorn.run("support_assistant.api:app")
    elif args.command == "ask":
        from langchain_core.tracers.langchain import wait_for_all_tracers

        from support_assistant.graph import run
        from support_assistant.models import SupportRequest

        response = asyncio.run(run(SupportRequest(customer_id=args.customer, message=args.message)))
        wait_for_all_tracers()
        print(response.model_dump_json(indent=2))
    else:
        from support_assistant.evaluate import main as evaluate

        evaluate()
