"""Run cancellation (roadmap §10.2: 用户可中止 Run,未执行 Case 标记 CANCELED).

A run registers itself when its record is created (status=running, before any
case executes); any other request — dashboard, CLI, CI job — can cancel it by
id while it is in flight. Cases already completed keep their results (partial
results are preserved); cases not yet started become CANCELED.
"""
import logging

logger = logging.getLogger("specagent.cancellation")


class CancelRegistry:
    def __init__(self):
        self._active: set[str] = set()
        self._canceled: set[str] = set()

    def register(self, run_id: str) -> None:
        self._active.add(run_id)

    def unregister(self, run_id: str) -> None:
        self._active.discard(run_id)
        self._canceled.discard(run_id)

    def cancel(self, run_id: str) -> bool:
        """Request cancellation; False if the run is unknown or already finished."""
        if run_id in self._active:
            self._canceled.add(run_id)
            logger.info("cancellation requested for run %s", run_id)
            return True
        return False

    def is_canceled(self, run_id: str) -> bool:
        return run_id in self._canceled
