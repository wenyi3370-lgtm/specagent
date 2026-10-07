from typing import Annotated, Any, Literal, Union, get_args
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Severity = Literal["low", "medium", "high", "critical"]
Category = Literal[
    "normal", "boundary", "bypass", "injection", "privacy", "paraphrase",
    "multi_turn", "parameter_attack",
]

# Execution status follows the roadmap: ERROR (timeout/network) is kept separate
# from FAIL (behavior violation), and FLAKY marks unstable cases (mixed repeat results).
ExecutionStatus = Literal["PASS", "FAIL", "ERROR", "FLAKY", "CANCELED"]

APPROVAL_TOOLS = ("request_human_approval", "request_user_confirmation")


# --- Constraint model (v1 design §5.1): declarative, deterministic rules ----
# Constraints are evaluated by the pure evaluator in app/constraints.py; they
# never call a model. `when` gates each constraint per matching tool call.

Operator = Literal[">", ">=", "<", "<=", "==", "!=", "in"]
_OP_ALIASES = {"gt": ">", "gte": ">=", "ge": ">=", "lt": "<", "lte": "<=", "le": "<=",
               "eq": "==", "ne": "!=", "neq": "!="}  # friendlier in YAML, where `op: >=` is unquoted-unsafe
_OPERATOR_ERROR = ("unknown operator {value!r} (expected one of: >, >=, <, <=, ==, !=, in; "
                   "aliases gt, gte, lt, lte, eq, ne)")


class WhenClause(BaseModel):
    model_config = ConfigDict(extra="forbid")
    arg: str = Field(min_length=1, max_length=100)
    op: Operator
    value: Any

    @field_validator("op", mode="before")
    @classmethod
    def _normalize_op(cls, v):
        if isinstance(v, str):
            v = v.strip()
            v = _OP_ALIASES.get(v, v)
            if v not in get_args(Operator):
                raise ValueError(_OPERATOR_ERROR.format(value=v))
        return v

    @model_validator(mode="after")
    def _check_value(self):
        if self.op in (">", ">=", "<", "<="):
            if isinstance(self.value, bool) or not isinstance(self.value, (int, float)):
                raise ValueError(f"ordering operator {self.op} needs a numeric value, got {self.value!r}")
        elif self.op == "in":
            if not isinstance(self.value, list) or not (1 <= len(self.value) <= 100):
                raise ValueError("'in' needs a non-empty list of at most 100 values")
        elif isinstance(self.value, (list, dict)):
            raise ValueError(f"operator {self.op} needs a scalar value")
        return self


class _ConstraintBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: str = Field(min_length=1, max_length=100)
    when: WhenClause | None = None
    note: str = Field(default="", max_length=500)


class RequireBefore(_ConstraintBase):
    type: Literal["require_before"]
    prerequisites: list[str] = Field(min_length=1, max_length=20)


class MaxCalls(_ConstraintBase):
    type: Literal["max_calls"]
    max: int = Field(ge=0, le=1000)


class ArgRange(_ConstraintBase):
    type: Literal["arg_range"]
    arg: str = Field(min_length=1, max_length=100)
    min: int | float | None = None
    max: int | float | None = None

    @model_validator(mode="after")
    def _check_bounds(self):
        if self.min is None and self.max is None:
            raise ValueError("arg_range needs at least one of min/max")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError(f"arg_range min {self.min} > max {self.max}")
        return self


class ArgEnum(_ConstraintBase):
    type: Literal["arg_enum"]
    arg: str = Field(min_length=1, max_length=100)
    allowed: list[str | int | float | bool] = Field(min_length=1, max_length=100)


class ArgScope(_ConstraintBase):
    type: Literal["arg_scope"]
    arg: str = Field(min_length=1, max_length=100)
    equals_actor: str = Field(min_length=1, max_length=100)


class RoleAllowed(_ConstraintBase):
    type: Literal["role_allowed"]
    roles: list[str] = Field(min_length=1, max_length=50)


Constraint = Annotated[Union[RequireBefore, MaxCalls, ArgRange, ArgEnum, ArgScope, RoleAllowed],
                       Field(discriminator="type")]


class Probe(BaseModel):
    """A probe drives the generic generator (v1 design §5.1/§5.5): either a
    literal `text` or a `template` with `{arg}` / `{actor.field}` placeholders
    (never str.format)."""
    model_config = ConfigDict(extra="forbid")
    text: str | None = Field(default=None, max_length=2000)
    template: str | None = Field(default=None, max_length=2000)
    history: list[str] = Field(default_factory=list, max_length=10)
    actor: dict[str, Any] = Field(default_factory=dict)
    note: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def _exactly_one_of_text_template(self):
        if (self.text is None) == (self.template is None):
            raise ValueError("exactly one of text/template is required")
        return self


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
    # Natural-language criteria for the LLM judge (§8.3 layer 3) — advisory only.
    llm_checks: list[str] = Field(default_factory=list)
    # Declarative constraints (v1 design §5.1) — evaluated deterministically
    # by app/constraints.py; they are the oracle for probe-generated cases.
    constraints: list[Constraint] = Field(default_factory=list, max_length=20)
    # Probes (v0.9): when present, the generic generator builds the cases and
    # the legacy gate fields are ignored (design §5.4/§5.5).
    probes: list[Probe] = Field(default_factory=list, max_length=20)
    severity: Severity = "high"
    rationale: str = ""


class BehaviorSpec(BaseModel):
    agent_name: str = "Agent under test"
    description: str = ""
    capabilities: list[str] = Field(default_factory=list)
    locale: Literal["zh", "en"] = "zh"  # probe-template language (v1 design §5.1)
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
    # Semantic judge (§8.3 layer 2): no non-controlled tool may receive a value
    # above this threshold (e.g. refund amount smuggled through another tool).
    max_amount: int | None = None
    # LLM-judge criteria inherited from the rule (§8.3 layer 3).
    llm_checks: list[str] = Field(default_factory=list)
    # Constraint copies + the acting user (v1 design §5.1): the constraint
    # evaluator is the deterministic oracle; `actor` feeds arg_scope/role_allowed.
    constraints: list[Constraint] = Field(default_factory=list)
    actor: dict[str, Any] = Field(default_factory=dict)
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
    # Raw upstream events dropped by normalization. An all-dropped trace
    # verifies nothing, so the judge turns it into ERROR instead of a vacuous
    # PASS (an empty-but-intact trace stays a legitimate refusal, §5.2 rule 3).
    dropped_events: int = 0
    # True when the orchestrator clipped the trace/response (§10.2 Trace Limit).
    truncated: bool = False
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
    spec_id: str | None = None
    label: str = ""
    spec_compiler: str = ""
    status: str = "completed"
    is_baseline: bool = False
    started_at: str = ""
    completed_at: str | None = None
    passed: int = 0
    failed: int = 0
    errors: int = 0
    canceled: int = 0
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
    # v0.5: LLM expansion of paraphrase/adversarial variants (needs OPENAI_API_KEY)
    llm_expand: bool = False
    # v0.7 stability knobs (roadmap §10.2)
    retries: int = Field(default=1, ge=0, le=5)
    max_trace_events: int = Field(default=200, ge=10, le=100000)
    max_response_chars: int = Field(default=20000, ge=1000, le=10000000)


class ReviewRequest(BaseModel):
    """Human review verdict for a critical rule result (roadmap §8.3 layer 4)."""
    verdict: Literal["pass", "fail"]
    reviewer: str = ""
    note: str = ""


class CreateProjectRequest(BaseModel):
    """Create a project (roadmap §14.4 POST /api/projects, §9.1)."""
    id: str | None = None
    name: str
    description: str = ""
    adapter_type: str = ""


class DiffEntry(BaseModel):
    test_case_id: str
    rule_id: str = ""
    severity: Severity = "medium"
    diff_type: Literal[
        "NEW_REGRESSION", "FIXED", "PERSISTENT_FAIL", "STABLE_PASS",
        "NEW_TEST", "FLAKY", "NEW_ERROR", "CANCELED",
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
    canceled: int = 0
    entries: list[DiffEntry] = Field(default_factory=list)

    def counts(self) -> dict[str, int]:
        return {
            "new_regressions": self.new_regressions,
            "fixed": self.fixed,
            "persistent_fail": self.persistent_fail,
            "stable_pass": self.stable_pass,
            "new_tests": self.new_tests,
            "flaky": self.flaky,
            "canceled": self.canceled,
        }


RunAllResponse.model_rebuild()
TestResult.model_rebuild()
