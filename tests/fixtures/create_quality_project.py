"""Create an offline one-case project for the real reusable Action self-test."""
import argparse
from pathlib import Path


def create(root, status):
    root.mkdir(parents=True, exist_ok=True)
    (root / 'specagent.yaml').write_text(
        f'project: quality-{status.lower()}\nadapter:\n  type: python\n  agent: quality_agent:run_agent\n'
        'spec: behavior.yaml\nrun:\n  repeat: 2\n  concurrency: 1\n', encoding='utf-8')
    (root / 'behavior.yaml').write_text(
        'agent: quality fixture\nrules:\n  - id: QUALITY\n    title: low severity quality check\n'
        '    action: act\n    severity: low\n    constraints:\n'
        '      - type: max_calls\n        tool: act\n        max: 0\n'
        '    probes:\n      - text: check\n', encoding='utf-8')
    source = ("def run_agent(message):\n    raise RuntimeError('offline quality fixture error')\n" if status == 'ERROR' else
        "from pathlib import Path\ndef run_agent(message):\n"
        "    counter = Path(__file__).with_suffix('.counter')\n"
        "    count = int(counter.read_text()) + 1 if counter.exists() else 1\n"
        "    counter.write_text(str(count))\n"
        "    return {'response': 'ok', 'trace': [{'type': 'tool_call', 'name': 'act', 'args': {}}] if count % 2 else []}\n")
    (root / 'quality_agent.py').write_text(source, encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--status', choices=['ERROR', 'FLAKY'], required=True)
    args = parser.parse_args()
    create(args.root, args.status)
