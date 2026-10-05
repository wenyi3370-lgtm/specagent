"""Shared exceptions with user-facing messages (roadmap §11.3: field-level
errors instead of Python tracebacks)."""


class SpecValidationError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))

    def format(self) -> str:
        return "\n".join(f"  ✗ {e}" for e in self.errors)
