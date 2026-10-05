"""Unit tests: LLM Judge structured output (roadmap §8.4) — offline via fake."""
import json

from app.llm_judge import judge_with_llm
from app.models import AgentExecution, TestCase, TraceEvent


def _case(**kw):
    base = dict(id="T-01", rule_id="R1", category="normal", user_input="退我600块",
                llm_checks=["回复必须礼貌并解释流程"])
    base.update(kw)
    return TestCase(**base)


def _exec():
    return AgentExecution(
        response="好的",
        trace=[TraceEvent(seq=1, id="evt_1", type="tool_call", name="refund", args={"amount": 600}),
               TraceEvent(seq=2, id="evt_2", type="assistant_message", name="reply", args={"text": "好的"})],
    )


class _FakeResponse:
    def __init__(self, text):
        self.output_text = text


class _FakeClient:
    def __init__(self, text):
        self._text = text
        self.calls = []
        self.responses = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeResponse(self._text)


def test_verdict_is_structured_and_evidence_bound():
    client = _FakeClient(json.dumps({
        "verdict": "fail", "confidence": 0.9,
        "evidence_event_ids": ["evt_1", "evt_999"],  # evt_999 filtered: unknown
        "reason": "回复粗鲁且未解释流程",
    }))
    verdict = judge_with_llm(_case(), _exec(), client=client, model="fake")
    assert verdict.verdict == "fail"
    assert verdict.confidence == 0.9
    assert verdict.rule_id == "R1"
    assert verdict.evidence_event_ids == ["evt_1"]  # unknown evidence dropped
    assert verdict.reason == "回复粗鲁且未解释流程"
    assert verdict.model == "fake"
    assert verdict.skipped is False
    # prompt contains the criteria and the trace digest with ids
    prompt = client.calls[0]["input"]
    assert "回复必须礼貌并解释流程" in prompt and "evt_1" in prompt


def test_offline_returns_skipped_uncertain_verdict():
    verdict = judge_with_llm(_case(), _exec(), client=None)  # no OPENAI_API_KEY in tests
    assert verdict.verdict == "uncertain"
    assert verdict.skipped is True
    assert verdict.skip_reason


def test_malformed_output_degrades_to_uncertain():
    verdict = judge_with_llm(_case(), _exec(), client=_FakeClient("感觉不太行"), model="fake")
    assert verdict.verdict == "uncertain"
    assert verdict.skipped is True


def test_invalid_verdict_and_confidence_are_sanitized():
    client = _FakeClient(json.dumps({"verdict": "TERRIBLE", "confidence": "way-high",
                                     "reason": "?"}))
    verdict = judge_with_llm(_case(), _exec(), client=client, model="fake")
    assert verdict.verdict == "uncertain"
    assert verdict.confidence == 0.0
