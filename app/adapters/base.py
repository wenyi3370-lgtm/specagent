"""Adapter contract (roadmap §7.2).

    class AgentAdapter:
        async def execute(self, test_case, context) -> AgentExecution

Adapters translate a framework-specific agent into the unified trace event
schema. They must not:
  - decide PASS/FAIL (that is app.judge),
  - interpret business rules,
  - keep a per-framework judge.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ..models import AgentExecution, TestCase


@dataclass
class ExecutionContext:
    """Per-case runtime context handed to every adapter."""
    test_case_id: str
    timeout_seconds: int = 30
    run_id: str | None = None
    metadata: dict = field(default_factory=dict)


class AgentAdapter(ABC):
    name: str = "adapter"

    @abstractmethod
    async def execute(self, case: TestCase, context: ExecutionContext) -> AgentExecution:
        """Run the agent on one test case and return a normalized execution.

        Implementations should raise on failure — the orchestrator converts
        exceptions/timeouts into per-case ERROR results (roadmap §10.2).
        """
