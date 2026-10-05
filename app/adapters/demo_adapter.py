"""Built-in demo agent adapter (deterministic, offline, two variants)."""
import asyncio
import os

from ..agents.demo import run_demo_agent
from ..models import AgentExecution, TestCase
from ..trace import ensure_ids
from .base import AgentAdapter, ExecutionContext


class DemoAdapter(AgentAdapter):
    name = "demo"

    def __init__(self, variant: str | None = None):
        self.variant = variant or os.getenv("DEMO_AGENT_VARIANT", "vulnerable")
        self.name = f"demo:{self.variant}"

    async def execute(self, case: TestCase, context: ExecutionContext) -> AgentExecution:
        # The demo agent is single-turn and stateless: history is ignored.
        execution = await asyncio.to_thread(run_demo_agent, case.user_input, self.variant)
        execution.trace = ensure_ids(execution.trace)
        return execution
