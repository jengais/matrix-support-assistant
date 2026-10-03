"""Runs the test cases through the real workflow, grades them, and writes results/eval_report.md.

A case may set "simulate": "order_service_down" to test the outage fallback.
"""

import asyncio
import json
from datetime import date
from pathlib import Path

from langchain_core.tracers.langchain import wait_for_all_tracers

from support_assistant.graph import run
from support_assistant.models import SupportRequest, SupportResponse


def grade(expected: dict, response: SupportResponse) -> list[str]:
    failures = []
    actual_request = response.service_request.type if response.service_request else None
    checks = [
        ("intent", response.intent in expected["intent"], response.intent),
        ("action", response.action == expected["action"], response.action),
        ("order_id", response.order_id == expected["order_id"], response.order_id),
        ("service_request", actual_request == expected["service_request"], actual_request),
        ("source_ids", set(expected["source_ids"]) <= set(response.source_ids), response.source_ids),
    ]
    failures += [f"{name}={actual}" for name, ok, actual in checks if not ok]
    failures += [
        f"reply missing {t!r}" for t in expected.get("reply_must_contain", []) if t not in response.reply
    ]
    failures += [
        f"reply leaks {t!r}" for t in expected.get("reply_must_not_contain", []) if t in response.reply
    ]
    for exp in expected.get("items", []):  # several questions: check each one
        key = (exp["order_id"], exp["intent"])
        item = next((i for i in response.items if (i.order_id, i.intent) == key), None)
        if item is None:
            failures.append(f"{key[0]}/{key[1]} not handled")
            continue
        item_request = item.service_request.type if item.service_request else None
        if (item.action, item_request) != (exp["action"], exp["service_request"]):
            failures.append(f"{key[0]}/{key[1]}={item.action}/{item_request}")
        if not set(exp["source_ids"]) <= set(item.source_ids):
            failures.append(f"{key[0]}/{key[1]} source_ids={item.source_ids}")
    return failures


def _expected_requests(expected: dict) -> str:
    """Every request the case should open: one per question when the message holds several."""
    types = [i["service_request"] for i in expected.get("items", [])] or [expected["service_request"]]
    return "<br>".join(t for t in types if t) or "—"


def _actual_requests(response: SupportResponse) -> str:
    refs = [i.service_request for i in response.items] or [response.service_request]
    return "<br>".join(f"{r.type} `{r.request_id}`" for r in refs if r) or "—"


async def _run_all(cases: list[dict]) -> list[SupportResponse]:
    return await asyncio.gather(
        *(
            run(
                SupportRequest(customer_id=c["customer_id"], message=c["message"]),
                today=date.fromisoformat(c["date"]),
                order_service_down=c.get("simulate") == "order_service_down",
                trace_name=f"{c['id']}: {c['message'][:40]}",
            )
            for c in cases
        )
    )


def main(cases_file: Path = Path("data/test_cases.json"), out_dir: Path = Path("results")) -> None:
    cases = json.loads(cases_file.read_text(encoding="utf-8"))
    expected = json.loads(Path("data/expected_results.json").read_text(encoding="utf-8"))
    responses = asyncio.run(_run_all(cases))
    wait_for_all_tracers()  # upload all LangSmith traces before the script exits

    rows, details, passed = [], [], 0
    for case, response in zip(cases, responses, strict=True):
        exp = expected[case["id"]]
        failures = grade(exp, response)
        passed += not failures
        status = "PASS" if not failures else "FAIL " + "; ".join(failures)
        rows.append(
            f"| {case['id']} | {case['message']} | {exp['action']} | {_expected_requests(exp)} "
            f"| {response.action} | {_actual_requests(response)} "
            f"| {'✅' if not failures else '❌ ' + status[5:]} |"
        )
        details.append(
            f"### {case['id']}: {case['message']}\n\n- reply: {response.reply}\n"
            f"- sources: {', '.join(response.source_ids) or '—'}\n- reason: {response.reason}\n"
        )
        print(f"{case['id']:>4}  {response.action:<9} {status}")

    out_dir.mkdir(exist_ok=True)
    (out_dir / "eval_results.json").write_text(
        json.dumps([r.model_dump(mode="json") for r in responses], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    report = [
        f"# Evaluation: {passed}/{len(cases)} passed\n",
        "| ID | Input | Expected action | Expected requests | Actual action | Actual requests | Pass |",
        "|---|---|---|---|---|---|---|",
        *rows,
        "\n## Replies\n",
        *details,
    ]
    (out_dir / "eval_report.md").write_text("\n".join(report), encoding="utf-8")
    print(f"\n{passed}/{len(cases)} passed. Report: {out_dir / 'eval_report.md'}")
