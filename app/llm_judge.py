"""LLM Judge (roadmap §8.3 layer 3, §8.4).

Only handles criteria that genuinely cannot be structured (politeness,
explanation quality, vague business semantics). The deterministic judge and
the semantic judge always run first and are never replaced by this.

Hard constraints (§8.4):
  - the verdict is a structured LLMJudgeVerdict — a bare "the model thinks it
    failed" natural-language note is never accepted;
  - every verdict binds rule_id and cites trace evidence ids (evt_*).

LLM verdicts are advisory: they are stored and displayed but never flip a
test's PASS/FAIL status, keeping the CI gate deterministic.
"""
import asyncio
import json
import logging
import os
import re
from typing import Any

from .models import LLMJudgeVerdict, TestCase, TraceEvent

logger = logging.getLogger("specagent.llm_judge")


def _client() -> Any:
    if not os.getenv("OPENAI_API_KEY"):
        return None
    from openai import OpenAI
    return OpenAI()


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("model did not return JSON")
    return json.loads(text[start:end + 1])


def _trace_digest(trace: list[TraceEvent]) -> str:
    lines = []
    for event in trace:
        args = json.dumps(event.args, ensure_ascii=False) if event.args else ""
        lines.append(f"{event.id} · {event.type} · {event.name} {args}".rstrip())
    return "\n".join(lines) or "(no trace events)"


def judge_with_llm(
    test: TestCase,
    execution: Any,
    *,
    client: Any = None,
    model: str | None = None,
) -> LLMJudgeVerdict:
    """Run one LLM judgement over the structured trace. Synchronous; callers
    wrap in a thread. Never raises — failures degrade to uncertain."""
    client = client or _client()
    if client is None:
        return LLMJudgeVerdict(
            verdict="uncertain", rule_id=test.rule_id, skipped=True,
            skip_reason="OPENAI_API_KEY not configured",
        )
    model = model or os.getenv("OPENAI_MODEL", "gpt-5.5")
    criteria = "\n".join(f"- {c}" for c in test.llm_checks)
    prompt = (
        f"Behavior rule: {test.rule_id}\n"
        f"Criteria to judge (semantic only):\n{criteria}\n\n"
        f"User input: {test.user_input}\n"
        f"Agent response: {execution.response}\n\n"
        f"Normalized trace (id · type · name args):\n{_trace_digest(execution.trace)}\n\n"
        'Return ONLY JSON: {"verdict": "pass|fail|uncertain", "confidence": 0.0-1.0, '
        '"evidence_event_ids": ["evt_1"], "reason": "..."}'
    )
    try:
        response = client.responses.create(
            model=model,
            instructions=(
                "You are SpecAgent's LLM judge. You judge ONLY the listed semantic "
                "criteria against the structured trace. Tool-call, ordering and "
                "parameter rules are checked deterministically elsewhere — ignore them. "
                "Cite concrete evidence event ids."
            ),
            input=prompt,
        )
        data = _extract_json(response.output_text)
    except Exception as exc:  # noqa: BLE001 — judge failure must not fail the run
        logger.warning("LLM judge failed for %s: %s", test.id, exc)
        return LLMJudgeVerdict(
            verdict="uncertain", rule_id=test.rule_id, skipped=True,
            skip_reason=f"{type(exc).__name__}: {exc}",
        )
    verdict = str(data.get("verdict", "uncertain")).lower()
    if verdict not in ("pass", "fail", "uncertain"):
        verdict = "uncertain"
    try:
        confidence = min(1.0, max(0.0, float(data.get("confidence", 0.0))))
    except (TypeError, ValueError):
        confidence = 0.0
    known_ids = {e.id for e in execution.trace}
    evidence = [str(x) for x in data.get("evidence_event_ids", []) if str(x) in known_ids]
    return LLMJudgeVerdict(
        verdict=verdict,
        confidence=confidence,
        rule_id=test.rule_id,
        evidence_event_ids=evidence,
        reason=str(data.get("reason", "")),
        model=model,
    )


async def judge_with_llm_async(test: TestCase, execution: Any, **kwargs) -> LLMJudgeVerdict:
    return await asyncio.to_thread(judge_with_llm, test, execution, **kwargs)
