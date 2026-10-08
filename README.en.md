# SpecAgent (English summary)

[![tests](https://github.com/wenyi3370-lgtm/specagent/actions/workflows/tests.yml/badge.svg)](https://github.com/wenyi3370-lgtm/specagent/actions/workflows/tests.yml)
[![specagent-gate](https://github.com/wenyi3370-lgtm/specagent/actions/workflows/specagent-gate.yml/badge.svg)](https://github.com/wenyi3370-lgtm/specagent/actions/workflows/specagent-gate.yml)
[![action-selftest](https://github.com/wenyi3370-lgtm/specagent/actions/workflows/action-selftest.yml/badge.svg)](https://github.com/wenyi3370-lgtm/specagent/actions/workflows/action-selftest.yml)

This is the only deliberately English document in the repository. The full documentation is Chinese: see [README.md](README.md).

**AI proposes, rules verify.** SpecAgent is behavior-driven testing and regression detection for AI agents. You write how the agent must behave (YAML rules, including conditional constraints on tool-call arguments). SpecAgent generates attack cases from those rules, runs your agent, checks its **tool trace**, diffs the result against a recorded baseline and fails CI when a change introduces a new critical regression.

![Demo: baseline → the broken agent is blocked by the gate → deterministic triage → fix verified (67 s, fully offline)](docs/assets/demo.gif)

See the intentionally-red demo PR [#3](https://github.com/wenyi3370-lgtm/specagent/pull/3) for what the gate looks like on a real GitHub pull request.

Every PASS/FAIL comes from deterministic code (`app/judge.py`, `app/constraints.py`). An LLM is never the judge.

## What it is (and is not)

- Stronger elsewhere: promptfoo and agentevals cover more providers and eval types and are more mature. SpecAgent is a narrow pipeline: rule, generated attack cases, baseline-diff gate (`NEW_REGRESSION` / `FIXED` / `PERSISTENT_FAIL` / `FLAKY`, severity-gated CI), agent triage and fix suggestions, deterministic re-verification.
- Semantics it pins down: conditional constraints on trace arguments (`require_before`, `max_calls`, `arg_range`, `arg_enum`, `arg_scope`, `role_allowed`) and approval-denied handling (a tool executed after an explicit denial is a violation).

## Quickstart (FinCare example, no API key needed)

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
python -m pip install -U pip     # pip 21.3+ is required for `pip install -e .` with pyproject.toml
pip install -e .

# 1. Baseline: the fixed agent passes everything (exit 0)
specagent run --config examples/fincare-agent/specagent.baseline.yaml --set-baseline --db fincare.db
# 2. Candidate: the vulnerable agent regresses on two critical rules (exit 1)
specagent run --config examples/fincare-agent/specagent.yaml --db fincare.db
# 3. Deterministic triage (no LLM)
specagent triage --config examples/fincare-agent/specagent.yaml --db fincare.db
# 4. Fix examples/fincare-agent/agent.py (compare agent_fixed.py), then verify
#    (quick way: cp examples/fincare-agent/agent_fixed.py examples/fincare-agent/agent.py,
#     then `git checkout examples/fincare-agent/agent.py` to restore afterwards)
specagent verify --config examples/fincare-agent/specagent.yaml --db fincare.db   # ALL_FIXED, exit 0
```

Exit codes: `0` ok, `1` gate failed, `2` configuration error, `4` agent session aborted or a confirmation was refused.

`specagent run --fail-on critical,high` overrides `gate.fail_on` for that invocation only.

## The agent and the data boundary

`specagent agent` and `specagent draft` use an LLM to draft specs, run suites, triage failures and propose fixes. Tools have risk tiers:

- auto: read-only inspection, triage, draft writing (drafts only);
- confirm: `run_suite`, `verify_fix`, `write_fix_suggestion` (`--yes` may auto-approve these);
- human_only: `replace_spec`, `set_baseline`. `--yes` can never approve them, because the spec and the baseline define what counts as correct; an agent that could rewrite them unattended could wave its own change through the gate.

What is sent to the LLM provider: rules, redacted traces, diffs, metrics, drafts, and, for `specagent draft`, the natural-language text you type. Source files are sent only with `--allow-source` (or `agent.allow_source: true`), inside a path sandbox and redacted. Never sent: `.env` files, keys, database URLs. Transcripts in `.specagent/agent-logs/` are redacted and ignored by git.

Without `OPENAI_API_KEY` the agent runs a fixed offline workflow (validate, run, triage, summary) and `draft` falls back to the deterministic compiler.

Model: `SPECAGENT_AGENT_MODEL`, then `OPENAI_MODEL`, then a built-in default (`gpt-5.5`) that this repository cannot verify. Set a model your account can use. `OPENAI_BASE_URL` is supported.

### Dashboard

Start it against the CLI's database: `SPECAGENT_DB=fincare.db uvicorn app.main:app` and open `http://127.0.0.1:8000` — run history, regression diff with side-by-side traces, baseline switching.

**Run the configured project from the web**: point `SPECAGENT_PROJECT_CONFIG` at your `specagent.yaml` (default `./specagent.yaml`). The target-agent bar at the top shows the project, adapter, rule count and generated case count, and **Run project suite** executes one round with the server-side adapter, `run.*` settings and rules file, printing the same gate verdict as the CLI (e.g. `Gate: FAILED — 4 new regression(s) at or above critical, high`). Integration happens through the config file; the web page runs what is configured and shows results — the browser can never choose the rules file, adapter, outbound address or project. Without a config the page says so and the demo button below tests only the built-in demo agent.

Security note: running the project suite executes the configured agent code in the server process (python/openai/langgraph adapters) or contacts the configured target, so **set `SPECAGENT_API_TOKEN` before exposing the server**. The new endpoint additionally requires `Content-Type: application/json` (cross-site forms cannot forge it) and, in no-token local mode, a loopback `Host` header (DNS-rebinding protection).


Project tools provides Validate, Run options, Triage, Verify, Export and Draft. Run options selects the configured baseline, the last run or an explicit run, and can set the new run as baseline. LLM expansion is opt-in and disabled without a key. Each operation shows its equivalent CLI command. History can compare any two runs.

Live progress shows completed/total cases, pass/fail/flaky/error/cancel counts and the most recently completed case. Dashboard runs, Verify and approved Agent runs share persistent progress snapshots. Reopening the dashboard can follow an active run and cancel it when cancellation is available. Running counts are provisional; final counts come from the shared judge. Older runs show their existing final summary.

Six metric trends support 7/30/90-day or all-time ranges, precise historical values and keyboard navigation from points to runs. Values reuse the shared metric functions over finished runs, including canceled runs. Rate axes stay at 0–100%; timestamps are UTC. Regression counts compare against the explicitly identified current baseline, so changing it recomputes comparisons; these are not saved historical CI gate decisions. Without a baseline, regressions are marked unevaluated. Charts show up to the latest 500 matching runs and disclose truncation. Existing CLI output is unchanged.

| CLI | Dashboard |
|---|---|
| `validate` | Validate: configuration, per-rule case counts and warnings |
| `run` | Run project suite and Run options |
| `baseline` | Set baseline in history or the run option |
| `diff` | Diff vs ★ / Diff vs… |
| `triage` | Triage in Project tools and history |
| `verify` | Verify against a pre-fix run or suggestion ID |
| `suggestions list/show` | Fix suggestions: diagnosis, files, diff, copy, download and verification history |
| `export` | Export JUnit / JSON with authenticated downloads |
| `report` | Report HTML download |
| `metrics` | Current metrics; read-only history charts are web-only |
| `draft` | Read-only YAML preview, Copy and Download |
| `agent` | Agent panel with existing approvals |
| `init` | CLI only: writes server configuration and rule files |

Validate imports configured agent code and therefore uses a guarded JSON POST. Draft never writes project files. With a key, requirements are sent to the configured LLM provider; otherwise (or on failure), the deterministic compiler is explicitly identified. Generated YAML is parsed again before delivery. New responses and downloads redact absolute server paths, credential URLs and sensitive environment values; CLI diagnostics retain their existing content. The browser cannot edit configuration, rules, endpoints or `gate.fail_on`.

The specification viewer shows the configured project's current YAML, saved behavior versions, compiled rules and source diffs. Copy/download use the displayed, redacted source. Versions and associated runs link to each other. Versions deduplicate compiled behavior: comment/formatting changes do not create a version, and saved source is the first source recorded for that behavior. The history list follows the selected project; current YAML follows server configuration. Source history/comparison are currently web-only read operations; existing CLI output is unchanged.

Projects includes a creation form for ID, name and description. The selector and table show names; the table also shows descriptions. A new project is selected automatically with empty run, spec and trend views. IDs are required in the form (up to 64 characters), names up to 128, descriptions up to 2000. Duplicate IDs return a conflict without overwriting records. Selecting a project filters history; Run project suite still tests the server-configured project. Adapter metadata does not configure execution. Creation writes only database metadata, never configuration files or adapter imports. Use `specagent init` locally and let the deployment owner configure `SPECAGENT_PROJECT_CONFIG` to connect an Agent.

Run history searches IDs, labels and Agent names, with status and baseline filters and pages of 25/50/100 records. Pagination reaches history beyond the previous 200-record window. Enter a baseline run ID to compare across pages. Labels can be edited or cleared. Deletion previews its impact and requires the exact run ID; current baselines and running records are protected. Deleted records leave default history, project counts and metrics, and can be restored from trash. Evidence, specs and suggestion references remain readable by ID, including report downloads. Search, label editing and trash are web features; existing CLI commands and output remain unchanged. See [validation](docs/run-management-validation.md).

Saved test cases offer repeat details. With `run.repeat > 1`, each repeat retains its deterministic status, violations, response, trace and latency. View individual repeats or compare any two, including FLAKY cases. The UI distinguishes original per-repeat judgments from the final suite status and marks canceled, unexecuted attempts. Older records and single executions have an explicit empty state. Reads and comparisons never execute the Agent. Displayed evidence is redacted and truncation is indicated. See [FLAKY validation](docs/flaky-details-validation.md).

### Dashboard agent panel

Fix suggestions preserves approved proposals for review. Approved tool cards link to their details. `suggestions show <id> --diff` and the authenticated download preserve the original diff bytes. Apply the relative `git apply` command locally in the project directory, then click Verify. The browser cannot apply patches. CLI and web share the verdict and save verification records beside the proposal.

The dashboard includes an agent panel (design task 16) with the same capabilities as `specagent agent`.

Enabling it: start the server with `SPECAGENT_API_TOKEN` set and enter the token in the dashboard; `/api/health` then reports `agent_enabled: true` and the panel appears. Without a token the whole agent API returns **403** (`agent_api_requires_token`). For local experiments only, `SPECAGENT_AGENT_API_INSECURE=1` enables it without a token for loopback clients (127.0.0.1 / ::1 / localhost); other clients still get 403, and a WARNING is logged at startup and on every request. Do not use it on an exposed server. The token check (401) runs before the enablement check (403).

Endpoints (sync, non-streaming):

- `POST /api/agent/sessions` with `{}`: creates a session; returns `session_id`, `mode` (`llm`, or `offline` without `OPENAI_API_KEY`), `model`, `project`.
- `POST /api/agent/sessions/{id}/messages` with `{"text": "…"}` (up to 4000 chars).
- `POST /api/agent/sessions/{id}/approve` with `{"action_id": "…", "approve": true|false}`.

Approvals: every confirm-tier call in the panel is parked; nothing runs until you click Approve, and Decline records the refusal. Human-only actions (`replace_spec`, `set_baseline`) are marked "needs you" and require ticking a "reviewed, approved by me" checkbox first (UI text is Chinese); the `approve` endpoint is their only human path. Sending a message while an action is pending, or resolving the same action twice, returns 409. If the draft or the current spec changed after the request, the result is `stale_confirmation` and nothing is written.

Data boundary: the browser cannot choose paths or enable `allow_source`. The project comes from the server-side `SPECAGENT_PROJECT_CONFIG` (default `./specagent.yaml`), `allow_source` comes only from that config, and unknown request fields are rejected. What reaches the LLM is the same as for the CLI. Panel runs appear in the dashboard's run history.

Sessions are in memory only: at most 8, 1-hour idle TTL, least recently used evicted first. A restart drops all sessions and parked actions; multiple processes do not share them.

## Limitations

This is a personal portfolio project, not a production-hardened service: one shared API token, no multi-user accounts. The GitHub Action and workflows are tested locally only. Automated tests use SQLite; the PostgreSQL path has had only light checks. Real-LLM features were verified against one compatible endpoint (DeepSeek) only.

## Docker

`docker compose up --build` starts the app and PostgreSQL 16 with no variables required. The port is bound to `127.0.0.1` only and the database uses a hard-coded development password: local use only. Before exposing it publicly, or whenever `OPENAI_API_KEY` is configured, set `SPECAGENT_API_TOKEN` to a random secret and serve it over HTTPS. The token is a password for that one deployment.

## GitHub Action

The repository ships a composite action (`action.yml`) and a self-test workflow. The action is deterministic: it never enables the agent and blanks `OPENAI_API_KEY`. It has only been tested locally (structure and helper logic), **not on GitHub**.

See [docs/architecture.md](docs/architecture.md), [docs/roadmap.md](docs/roadmap.md) and [docs/known-issues.md](docs/known-issues.md) (Chinese).

### Agent execution timeline and saved conversations

The Agent panel displays tool calls, results, approval pauses and resumptions as they happen. Model text arrives through Responses streaming; deterministic quotes remain complete and authoritative. History reads the sanitized JSONL files under `.specagent/agent-logs/`, supports pagination and download, and survives server restarts. Only a still-active server session can resume execution. Viewing an old approval never executes it. Disconnecting a stream does not replay an already approved action.
