"""A browser account's Agent remains inside its configured project."""
from .agent.tools import ToolRegistry


class AccountToolRegistry(ToolRegistry):
    def call(self, ctx, name, args, call_id):
        if isinstance(args, dict):
            for key in ('run_id', 'candidate', 'baseline', 'pre_run_id', 'pre_fix_run_id'):
                value = args.get(key)
                if isinstance(value, str) and value and not (key == 'baseline' and value == 'last'):
                    run = ctx.project.store.get_run(value)
                    if run is None or run['project_id'] != ctx.project.project_id:
                        return self._finish(ctx, name, call_id, {'ok': False, 'error': 'run_not_found'})
        return super().call(ctx, name, args, call_id)
