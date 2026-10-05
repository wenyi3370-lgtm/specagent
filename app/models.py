from typing import Any, Literal
from pydantic import BaseModel, Field

Severity = Literal["low", "medium", "high", "critical"]
Category = Literal["normal", "boundary", "bypass", "injection", "privacy", "paraphrase"]

# Execution status follows the roadmap: ERROR (timeout/network) is kept separate
# from FAIL (behavior violation), and FLAKY marks unstable cases (mixed repeat results).
ExecutionStatus = Literal["PASS", "FAIL", "ERROR", "FLAKY", "CANCELED"]

APPROVAL_TOOLS = ("request_human_approval", "request_user_confirmation")


class BehaviorRule(BaseModel):
    id: str
    title: str
    action: str
    condition: str = "always"
    require_calls: list[str] = Field(default_factory=list)
    forbid_calls: list[str] = Field(default_factory=list)
    # Tools that must not be called before an approval/confirmation call appears
    # earlier in the trace (roadmap 14.1: require tool X before tool Y).
    approval_for: list[str] = Field(default_factory=list)
    severity: Severity = "high"
    rationale: str = ""


class BehaviorSpec(BaseModel):
    agent_name: str = "Agent under test"
    description: str = ""
    capabilities: list[str] = Field(default_factory=list)
    rules: list[BehaviorRule] = Field(default_factory=list)
    compiler: str = "deterministic-demo"


class TestCase(BaseModel):
    id: str
    rule_id: str
    category: Category
    user_input: str
    # Prior user turns for multi-turn cases (roadmap §8.1); adapters forward it.
    history: list[str] = Field(default_factory=list)
    expected_calls: list[str] = Field(default_factory=list)
    forbidden_calls: list[str] = Field(default_factory=list)
    approval_for: list[str] = Field(default_factory=list)
    note: str = ""


class TraceEvent(BaseModel):
    """Normalized trace event (roadmap 7.1).

    Upstream adapters may omit id/seq/timestamp; the normalizer fills them in
    (`evt_<seq>`), so every judge verdict can cite concrete evidence ids.
    """
    id: str = ""
    seq: int = 0
    type: Literal[
        "user_message", "assistant_message", "tool_call", "tool_result",
        "approval_request", "approval_result", "error",
    ] = "assistant_message"
    name: str = ""
    args: dict[str, Any] = Field(default_factory=dict)
    result: Any = None
    timestamp: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentExecution(BaseModel):
    response: str = ""
    trace: list[TraceEvent] = Field(default_factory=list)
    latency_ms: int = 0
    error: str | None = None
    # Adapter-native payload kept for debugging (roadmap 7.2 "raw").
    raw: Any = None


class TestResult(BaseModel):
    test: TestCase
    passed: bool
    status: ExecutionStatus = "PASS"
    violations: list[str] = Field(default_factory=list)
    execution: AgentExecution
    latency_ms: int = 0
    execution_id: str | None = None
    # v0.5: structured LLM-judge verdict (advisory) + human review record.
    llm_verdict: "LLMJudgeVerdict | None" = None
    review: dict | None = None


class LLMJudgeVerdict(BaseModel):
    """LLM Judge output — roadmap §8.4 requires this exact structure: a bare
    natural-language 'the model thinks it failed' is never accepted."""
    verdict: Literal["pass", "fail", "uncertain"] = "uncertain"
    confidence: float = 0.0
    rule_id: str = ""
    evidence_event_ids: list[str] = Field(default_factory=list)
    reason: str = ""
    model: str = ""
    skipped: bool = False
    skip_reason: str = ""


class CompileRequest(BaseModel):
    text: str


class RunAllResponse(BaseModel):
    spec: BehaviorSpec
    tests: list[TestCase]
    results: list[TestResult]
    passed: int
    failed: int
    score: float
    run_id: str | None = None
    diff: "DiffSummary | None" = None


class RunSummary(BaseModel):
    id: str
    project_id: str
    label: str = ""
    spec_compiler: str = ""
    status: str = "completed"
    is_baseline: bool = False
    started_at: str = ""
    completed_at: str = ""
    passed: int = 0
    failed: int = 0
    errors: int = 0
    total: int = 0
    score: float = 0.0
    commit_sha: str | None = None
    agent: str = ""


class RunDetail(RunSummary):
    spec: BehaviorSpec
    tests: list[TestCase] = Field(default_factory=list)
    results: list[TestResult] = Field(default_factory=list)


class CreateRunRequest(BaseModel):
    text: str = ""
    spec: BehaviorSpec | None = None
    project_id: str = "default"
    label: str = ""
    agent: str = "auto"  # auto | demo | http; framework adapters come from specagent.yaml
    agent_variant: str | None = None
    repeat: int = 1
    concurrency: int = 4
    timeout_seconds: int = 30
    set_baseline: bool = False


class DiffEntry(BaseModel):
    test_case_id: str
    rule_id: str = ""
    severity: Severity = "medium"
    diff_type: Literal[
        "NEW_REGRESSION", "FIXED", "PERSISTENT_FAIL", "STABLE_PASS",
        "NEW_TEST", "FLAKY", "NEW_ERROR",
    ]
    baseline_status: str | None = None
    candidate_status: ExecutionStatus = "FAIL"
    input: str = ""
    expected: list[str] = Field(default_factory=list)
    violations: list[str] = Field(default_factory=list)
    baseline_trace: list[TraceEvent] = Field(default_factory=list)
    candidate_trace: list[TraceEvent] = Field(default_factory=list)


class DiffSummary(BaseModel):
    baseline_run_id: str | None = None
    candidate_run_id: str
    new_regressions: int = 0
    fixed: int = 0
    persistent_fail: int = 0
    stable_pass: int = 0
    new_tests: int = 0
    flaky: int = 0
    entries: list[DiffEntry] = Field(default_factory=list)

    def counts(self) -> dict[str, int]:
        return {
            "new_regressions": self.new_regressions,
            "fixed": self.fixed,
            "persistent_fail": self.persistent_fail,
            "stable_pass": self.stable_pass,
            "new_tests": self.new_tests,
            "flaky": self.flaky,
        }


RunAllResponse.model_rebuild()
TestResult.model_rebuild()
