"""Browser fixture: a slow scripted Responses stream, without external calls."""
import time
from types import SimpleNamespace

from app import agent_api
from app.main import app  # noqa: F401 — uvicorn entry point

TEXT = ("Inspecting the behavior rules and explaining each deterministic result. " * 4
        + "<img src=x onerror=alert(1)> is untrusted text. "
        + "This is a scripted browser test, not a real model response.")


class FakeClient:
    def __init__(self):
        self.responses = self

    def create(self, **kwargs):
        assert kwargs.get("stream") is True

        def events():
            for chunk in (TEXT[:180], TEXT[180:300], TEXT[300:]):
                yield SimpleNamespace(type="response.output_text.delta", delta=chunk)
                time.sleep(0.8)
            message = SimpleNamespace(type="message", content=[{"text": TEXT}])
            yield SimpleNamespace(type="response.completed", response=SimpleNamespace(output=[message]))

        return events()


agent_api.client_factory = FakeClient
