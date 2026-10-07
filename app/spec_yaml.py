"""Behavior Spec YAML loader (roadmap §14.1, v1.0 DoD: "YAML 可编辑 + schema validation";
v1 design §5.4 — task 7).

Example:
    agent: E-commerce Support Agent
    locale: zh
    rules:
      - id: LARGE_REFUND_APPROVAL
        title: 退款超过500元必须人工审批
        severity: critical
        action: refund
        condition: refund amount > 500
        require_calls: [request_human_approval]
        approval_for: [refund]
        constraints:
          - type: max_calls
            tool: refund
            max: 1
            when: {arg: amount, op: ">", value: 500}
        probes:
          - template: "请退款{amount}元"
            actor: {account_id: "ACC-1"}

parse_spec collects every rule-level error and raises them together as one
SpecValidationError with field-level messages. Unknown keys fail closed
against typos (§5.4): a near-miss of a known key or a bare constraint-type
name is an error; anything else is tolerated for forward compatibility and
reported as a warning by collect_spec_warnings().
"""
import difflib

import yaml
from pydantic import TypeAdapter, ValidationError

from .errors import SpecValidationError
from .models import BehaviorRule, BehaviorSpec, Constraint, Probe
from .probe_generator import _MAX_CASES, ProbeCapExceeded, cases_for

CONSTRAINT_TYPE_NAMES = ("require_before", "max_calls", "arg_range", "arg_enum",
                         "arg_scope", "role_allowed")
_CONSTRAINT_TYPE_SET = frozenset(CONSTRAINT_TYPE_NAMES)

KNOWN_RULE_KEYS = {"id", "title", "description", "action", "condition", "require_calls",
                   "forbid_calls", "approval_for", "llm_checks", "severity", "rationale",
                   "constraints", "probes"}
KNOWN_SPEC_KEYS = {"agent", "agent_name", "description", "capabilities", "rules", "locale"}

_CONSTRAINT_ADAPTER = TypeAdapter(Constraint)


def _format_validation_error(exc: ValidationError, where: str,
                             strip_tag: str | None = None) -> list[str]:
    out = []
    for err in exc.errors():
        loc = tuple(err["loc"])
        # Inside a discriminated union pydantic prefixes the tag ("max_calls",
        # "when", "op") — the caller already wrote constraints[j].
        if strip_tag and loc and loc[0] == strip_tag:
            loc = loc[1:]
        loc_str = ".".join(str(x) for x in loc) or "<root>"
        msg = err["msg"]
        # The op validator's message already carries its own "(expected one
        # of: …)" — appending the input again would duplicate it (§5.4).
        suffix = "" if "expected one of" in msg else f" (got {err['input']!r})"
        out.append(f"{where}.{loc_str}: {msg}{suffix}")
    return out


def _unknown_key_error(key: str, known: set[str], path: str) -> str | None:
    """Fail-closed typo check (§5.4): (a) a near-miss of a known key and
    (b) a bare constraint-type name are errors; None = tolerated (the key is
    dropped and reported as a forward-compatibility warning)."""
    if key in known:
        return None
    match = difflib.get_close_matches(key, sorted(known), n=1, cutoff=0.75)
    if match:
        return f"{path}.{key}: unknown key (did you mean '{match[0]}'?)"
    if key in _CONSTRAINT_TYPE_SET:
        return f"{path}.{key}: constraint types belong under 'constraints:' as '- type: {key}'"
    return None


def _parse_constraints(raw: dict, prefix: str, errors: list[str]) -> list:
    parsed: list = []
    if "constraints" not in raw:
        return parsed
    raw_constraints = raw["constraints"]
    if not isinstance(raw_constraints, list):
        errors.append(f"{prefix}.constraints: expected a list of constraint mappings "
                      f"(got {raw_constraints!r})")
        return parsed
    for j, rc in enumerate(raw_constraints):
        cprefix = f"{prefix}.constraints[{j}]"
        if not isinstance(rc, dict):
            errors.append(f"{cprefix}: expected a constraint mapping (got {rc!r})")
            continue
        tag = rc.get("type")
        if tag not in _CONSTRAINT_TYPE_SET:
            errors.append(f"{cprefix}.type: unknown constraint type {tag!r} "
                          f"(expected one of: {', '.join(CONSTRAINT_TYPE_NAMES)})")
            continue
        try:
            parsed.append(_CONSTRAINT_ADAPTER.validate_python(rc))
        except ValidationError as exc:
            errors.extend(_format_validation_error(exc, cprefix, strip_tag=tag))
    for k in range(1, len(parsed)):
        for j in range(k):
            if parsed[k].model_dump() == parsed[j].model_dump():
                errors.append(f"{prefix}.constraints[{k}]: duplicate of constraints[{j}]")
                break
    return parsed


def _constraint_facts(constraints: list) -> tuple[set[str], list[tuple[str, str]]]:
    """Args referenced by the rule's constraints (arg or when.arg) plus the
    (arg, equals_actor) pairs of arg_scope — the context probe templates are
    validated against (§5.4)."""
    known_args: set[str] = set()
    scope_pairs: list[tuple[str, str]] = []
    for c in constraints:
        if getattr(c, "arg", None):
            known_args.add(c.arg)
        if c.when is not None:
            known_args.add(c.when.arg)
        if c.type == "arg_scope":
            scope_pairs.append((c.arg, c.equals_actor))
    return known_args, scope_pairs


def _parse_probes(raw: dict, prefix: str, errors: list[str],
                  known_args: set[str], scope_pairs: list[tuple[str, str]]) -> list[Probe]:
    from .probe_generator import _PLACEHOLDER, _template_args

    parsed: list[Probe] = []
    if "probes" not in raw:
        return parsed
    raw_probes = raw["probes"]
    if not isinstance(raw_probes, list):
        errors.append(f"{prefix}.probes: expected a list of probe mappings (got {raw_probes!r})")
        return parsed
    known_sorted = ", ".join(sorted(known_args)) if known_args else "none"
    for j, rp in enumerate(raw_probes):
        pprefix = f"{prefix}.probes[{j}]"
        if not isinstance(rp, dict):
            errors.append(f"{pprefix}: expected a probe mapping (got {rp!r})")
            continue
        try:
            probe = Probe.model_validate(rp)
        except ValidationError as exc:
            errors.extend(_format_validation_error(exc, pprefix))
            continue
        parsed.append(probe)
        if probe.template is None:
            continue
        # Strict placeholder checks (§5.4) — never str.format, so {0.__class__}
        # cannot be smuggled through a template.
        placeholders: list[tuple[str, str | None]] = []
        for m in _PLACEHOLDER.finditer(probe.template):
            pair = (m.group(1), m.group(2))
            if pair not in placeholders:
                placeholders.append(pair)
        actor_keys = ", ".join(sorted(probe.actor)) or "none"
        for name, field in placeholders:
            if field is None:
                if name not in known_args:
                    errors.append(f"{pprefix}.template: unknown placeholder {{{name}}} "
                                  f"(known args: {known_sorted})")
            elif field not in probe.actor:
                errors.append(f"{pprefix}.template: placeholder {{actor.{field}}} requires "
                              f"probe.actor['{field}'] (actor provides: {actor_keys})")
        for arg, field in scope_pairs:
            if arg in _template_args(probe.template) and field not in probe.actor:
                errors.append(f"{pprefix}.template: arg_scope constraint on '{arg}' requires "
                              f"probe.actor to provide '{field}' so the scope can be evaluated")
    for k in range(1, len(parsed)):
        for j in range(k):
            if parsed[k].model_dump() == parsed[j].model_dump():
                errors.append(f"{prefix}.probes[{k}]: duplicate of probes[{j}]")
                break
    return parsed


def parse_spec(data: dict, where: str = "spec") -> BehaviorSpec:
    if not isinstance(data, dict):
        raise SpecValidationError([f"{where}: expected a mapping, got {type(data).__name__}"])
    errors: list[str] = []

    # Fail-closed unknown-key scan, spec level (§5.4): `rule:` suggests `rules`.
    for key in data:
        message = _unknown_key_error(key, KNOWN_SPEC_KEYS, where)
        if message:
            errors.append(message)

    locale = data.get("locale", "zh")
    if locale not in ("zh", "en"):
        errors.append(f"{where}.locale: locale must be 'zh' or 'en' (got {locale!r})")
        locale = "zh"  # keep going so later checks stay meaningful
    payload = {
        "agent_name": data.get("agent") or data.get("agent_name") or "Agent under test",
        "description": data.get("description", ""),
        "capabilities": data.get("capabilities", []),
        "locale": locale,
        "compiler": "yaml",
    }

    rules = data.get("rules", [])
    if not isinstance(rules, list) or not rules:
        errors.append(f"{where}.rules: at least one rule is required")
        rules = []

    parsed_rules = []
    for i, raw in enumerate(rules):
        prefix = f"{where}.rules[{i}]"
        if not isinstance(raw, dict):
            errors.append(f"{prefix}: expected a mapping")
            continue
        for key in raw:
            message = _unknown_key_error(key, KNOWN_RULE_KEYS, prefix)
            if message:
                errors.append(message)
        if not raw.get("id"):
            errors.append(f"{prefix}.id: rule id is required")
            continue
        parsed_constraints = _parse_constraints(raw, prefix, errors)
        known_args, scope_pairs = _constraint_facts(parsed_constraints)
        parsed_probes = _parse_probes(raw, prefix, errors, known_args, scope_pairs)
        rule_payload = {
            "id": raw["id"],
            "title": raw.get("title") or raw.get("description") or raw["id"],
            "action": raw.get("action", ""),
            "condition": raw.get("condition", "always"),
            "require_calls": raw.get("require_calls", []),
            "forbid_calls": raw.get("forbid_calls", []),
            "approval_for": raw.get("approval_for", []),
            "llm_checks": raw.get("llm_checks", []),
            "severity": raw.get("severity", "high"),
            "rationale": raw.get("rationale", ""),
            "constraints": parsed_constraints,
            "probes": parsed_probes,
        }
        try:
            rule = BehaviorRule.model_validate(rule_payload)
        except ValidationError as exc:
            errors.extend(_format_validation_error(exc, prefix))
            continue
        parsed_rules.append(rule)
        if rule.probes:
            # The generated-case cap is an error, never a truncation (§5.4).
            try:
                cases_for(rule, locale)
            except ProbeCapExceeded as exc:
                errors.append(f"{prefix}: rule '{rule.id}' would generate {exc.count} cases "
                              f"(> {_MAX_CASES}); split the rule or reduce probes/constraints")

    ids = [r.id for r in parsed_rules]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        errors.append(f"{where}.rules: duplicate rule ids: {', '.join(duplicates)}")

    payload["rules"] = parsed_rules
    spec = None
    try:
        spec = BehaviorSpec.model_validate(payload)
    except ValidationError as exc:
        errors.extend(_format_validation_error(exc, where))
    if errors:
        raise SpecValidationError(errors)
    return spec


def collect_spec_warnings(data: dict) -> list[str]:
    """Forward-compatibility warnings (§5.4): the unknown keys parse_spec
    tolerates — everything that is neither known nor one of the fail-closed
    typo classes. Pure; used by `validate` and the agent's inspect_project."""
    warnings: list[str] = []
    if not isinstance(data, dict):
        return warnings
    for key in data:
        if key in KNOWN_SPEC_KEYS or _unknown_key_error(key, KNOWN_SPEC_KEYS, ""):
            continue
        warnings.append(f"unknown top-level key '{key}' ignored")
    rules = data.get("rules", [])
    if isinstance(rules, list):
        for i, raw in enumerate(rules):
            if not isinstance(raw, dict):
                continue
            for key in raw:
                if key in KNOWN_RULE_KEYS or _unknown_key_error(key, KNOWN_RULE_KEYS, ""):
                    continue
                warnings.append(f"rules[{i}]: unknown key '{key}' ignored")
    return warnings


def dump_spec_yaml(spec: BehaviorSpec) -> str:
    """Serialize a spec back to YAML (needed by the agent's draft tooling,
    §5.4 / task 14): only non-default fields are written, `type` first inside
    each constraint. Round-trips through parse_spec."""
    data: dict = {}
    if spec.agent_name != "Agent under test":
        data["agent_name"] = spec.agent_name
    if spec.description:
        data["description"] = spec.description
    if spec.capabilities:
        data["capabilities"] = list(spec.capabilities)
    if spec.locale != "zh":
        data["locale"] = spec.locale
    rules_out = []
    for rule in spec.rules:
        entry = rule.model_dump(exclude_none=True, exclude_defaults=True)
        if "constraints" in entry:
            entry["constraints"] = [{"type": c["type"],
                                     **{k: v for k, v in c.items() if k != "type"}}
                                    for c in entry["constraints"]]
        rules_out.append(entry)
    data["rules"] = rules_out
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)


def load_spec_file(path: str) -> BehaviorSpec:
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except FileNotFoundError:
        raise SpecValidationError([f"spec file not found: {path}"]) from None
    except yaml.YAMLError as exc:
        raise SpecValidationError([f"{path}: invalid YAML: {exc}"]) from None
    return parse_spec(data if data is not None else {}, where=path)
