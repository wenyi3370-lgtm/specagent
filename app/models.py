from typing import Any, Literal
from pydantic import BaseModel, Field

Severity = Literal["low", "medium", "high", "critical"]
Category = Literal["normal", "boundary", "bypass", "injection", "privacy"]


class BehaviorRule(BaseModel):
    id: str
    title: str
    action: str
    condition: str = "always"
    require_calls: list[str] = Field(default_factory=list)
    forbid_calls: list[str] = Field(default_factory=list)
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
    expected_calls: list[str] = Field(default_factory=list)
    forbidden_calls: list[str] = Field(default_factory=list)
    note: str = ""


class TraceEvent(BaseModel):
    type: Literal["tool_call", "assistant_message"]
    name: str
    args: dict[str, Any] = Field(default_factory=dict)


class AgentExecution(BaseModel):
    response: str
    trace: list[TraceEvent] = Field(default_factory=list)


class TestResult(BaseModel):
    test: TestCase
    passed: bool
    violations: list[str] = Field(default_factory=list)
    execution: AgentExecution


class CompileRequest(BaseModel):
    text: str


class RunAllResponse(BaseModel):
    spec: BehaviorSpec
    tests: list[TestCase]
    results: list[TestResult]
    passed: int
    failed: int
    score: float
