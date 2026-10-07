"""Private browser fixture: generate a real approved proposal, without an LLM."""
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from app import agent_api
from app.main import app, store  # noqa: F401 — uvicorn entry point
from app.agent.sandbox import ProjectSandbox
from app.agent.tools import ConfirmResult, ToolContext, ToolRegistry
from app.project import Project, project_config_path, run_project

def prepare():
    project = Project.load(project_config_path(), store=store)
    pre = run_project(project)
    target = project.agent_path.split(":")[0] + ".py"
    context = ToolContext(project=project, sandbox=ProjectSandbox(project.root, True),
                          allow_source=True, confirm=lambda _: ConfirmResult("approved"))
    registry = ToolRegistry()
    assert registry.call(context, "read_file", {"path": target}, "read").payload["ok"]
    fixed = (project.root / "agent_fixed.py").read_text(encoding="utf-8")
    proposal = registry.call(context, "write_fix_suggestion", {
        "rule_id": "ACCOUNT_SCOPE", "diagnosis": "Use the authenticated account. <img src=x>",
        "pre_fix_run_id": pre.run_id, "changes": [{"path": target, "new_content": fixed}],
    }, "suggest").payload
    assert proposal["ok"], proposal
    return project, pre, target, fixed


with ThreadPoolExecutor(max_workers=1) as executor:
    project, pre, target, fixed = executor.submit(prepare).result()


class Item(SimpleNamespace):
    def model_dump(self):
        return dict(self.__dict__)


class FakeClient:
    def __init__(self):
        self.responses = self
        self.step = 0

    def create(self, **kwargs):
        assert kwargs.get("stream") is True
        self.step += 1
        if self.step == 1:
            args = {"rule_id": "ACCOUNT_SCOPE", "diagnosis": "Scripted fixture proposal",
                    "pre_fix_run_id": pre.run_id, "changes": [{"path": target, "new_content": fixed}]}
            output = [Item(type="function_call", name="read_file", call_id="read", arguments=json.dumps({"path": target})),
                      Item(type="function_call", name="write_fix_suggestion", call_id="write", arguments=json.dumps(args))]
        else:
            output = [Item(type="message", content=[{"text": "Scripted proposal saved for human review."}])]
        return iter([SimpleNamespace(type="response.completed", response=SimpleNamespace(output=output))])


agent_api.client_factory = FakeClient
