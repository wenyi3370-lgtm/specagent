"""Pure presentation metadata for tool calls absent from the baseline."""
import json
from collections import Counter


def new_call_indices(baseline, candidate) -> list[int]:
    """Compare name + arguments as a multiset, ignoring ids/timestamps/results.
    Indices refer to tool_call events only. Extra repeated calls are new."""
    def calls(events):
        return [e.model_dump() if hasattr(e, "model_dump") else e for e in events or []
                if (e.type if hasattr(e, "type") else e.get("type")) == "tool_call"]

    def signature(event):
        return json.dumps([event.get("name"), event.get("args") or {}], sort_keys=True, ensure_ascii=False)

    counts = Counter(signature(e) for e in calls(baseline))
    added = []
    for i, event in enumerate(calls(candidate)):
        key = signature(event)
        if counts[key]:
            counts[key] -= 1
        else:
            added.append(i)
    return added
