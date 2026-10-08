"""Explicit opt-in real experiment; bounded to one frozen 72-attempt ledger.

Run with --credential-prompt, or explicitly opt into --allow-local-env. Credential
values are read into memory; no environment variables/files are rewritten.
The output directory must be new or belong to this exact experiment.
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from experiments.judge_comparison import (INSTRUCTIONS, MAX_CALLS, MAX_OUTPUT_TOKENS,
    MODEL, REPEATS, TEMPERATURE, Ledger, deterministic, digest, load_corpus,
    parse_response, request_options, summarize)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    credential = parser.add_mutually_exclusive_group(required=True)
    credential.add_argument("--allow-local-env", action="store_true")
    credential.add_argument("--credential-prompt", action="store_true")
    parser.add_argument("--out", type=Path, default=Path("dist/judge-comparison"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    corpus = load_corpus(root / "experiments/judge-comparison/corpus.json")
    frozen = {"corpus_sha256": digest(corpus), "instructions_sha256": digest(INSTRUCTIONS),
              "model": MODEL, "repeats": REPEATS, "max_calls": MAX_CALLS,
              "temperature": TEMPERATURE, "reasoning": "none",
              "max_output_tokens": MAX_OUTPUT_TOKENS}
    args.out.mkdir(parents=True, exist_ok=True)
    lock_path = args.out / "runner.lock"
    lock = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        ledger = Ledger(args.out / "ledger.json", digest(frozen))
        frozen_path = args.out / "frozen.json"
        if frozen_path.exists() and json.loads(frozen_path.read_text()) != frozen:
            raise ValueError("Frozen protocol changed")
        frozen_path.write_text(json.dumps(frozen, indent=2), encoding="utf-8")
        # No echo, no command-line secret, no environment/file writes.
        if args.credential_prompt:
            import getpass
            if not sys.stdin.isatty():
                raise ValueError("Credential prompt requires a no-echo terminal")
            key = getpass.getpass("DeepSeek credential (no echo): ")
            base = "https://api.deepseek.com"
        else:
            from dotenv import dotenv_values
            config = dotenv_values(root / ".env", interpolate=False)
            key = config.get("DEEPSEEK_API_KEY") or config.get("OPENAI_API_KEY")
            base = config.get("DEEPSEEK_BASE_URL") or config.get("OPENAI_BASE_URL") or "https://api.deepseek.com"
            config.clear()
        url = urlsplit(base)
        if (not key or url.scheme != "https" or url.hostname != "api.deepseek.com"
                or url.username or url.password or url.query or url.fragment
                or url.port not in (None, 443) or url.path.rstrip("/") not in ("", "/v1")):
            raise ValueError("Expected local DeepSeek key and official HTTPS endpoint")
        from openai import OpenAI
        import httpx
        with OpenAI(api_key=key, base_url="https://api.deepseek.com", max_retries=0, timeout=30,
                    http_client=httpx.Client(trust_env=False, timeout=30)) as client:
            # Credential stays in the SDK client; not in process env or files.
            del key, base
            models = client.models.list()
            model = next((m for m in models.data if m.id == MODEL), None)
            if model is None or getattr(model, "name", None) != "DeepSeek-V4.1-Flash":
                raise ValueError("Requested V4.1 Flash model could not be verified")
            print("Verified DeepSeek-V4.1-Flash; API id deepseek-flash", flush=True)
            frozen["verified_model_name"] = "DeepSeek-V4.1-Flash"
            rows_path = args.out / "trials.jsonl"
            rows = [json.loads(s) for s in rows_path.read_text(encoding="utf-8").splitlines()] if rows_path.exists() else []
            completed = {(r["case_id"], r["repeat"], r["method"]) for r in rows}
            for case in corpus["cases"]:
                case_id = case["test"]["id"]
                for repeat in range(REPEATS):
                    for method in ("deterministic", "llm"):
                        if (case_id, repeat, method) in completed:
                            continue
                        row = {"case_id": case_id, "repeat": repeat, "method": method}
                        if method == "deterministic":
                            row.update(deterministic(case))
                        elif not ledger.reserve(case_id, repeat):
                            row.update(verdict="UNCERTAIN", failure="interrupted_attempt",
                                       reason="", evidence_event_ids=[], latency_ms=0)
                        else:
                            start = time.perf_counter()
                            try:
                                response = client.responses.create(**request_options(case))
                                row.update(parse_response(response, case))
                                row["response_model"] = response.model
                                usage = response.usage
                                row["usage"] = {"input_tokens": usage.input_tokens,
                                    "output_tokens": usage.output_tokens,
                                    "cached_input_tokens": getattr(usage.input_tokens_details, "cached_tokens", 0)}
                            except Exception as exc:
                                row.update(verdict="UNCERTAIN", failure=f"request_{type(exc).__name__}",
                                           reason="", evidence_event_ids=[])
                            row["latency_ms"] = (time.perf_counter() - start) * 1000
                        # Corpus is synthetic; still remove any credential echo.
                        from app.agent.sandbox import redact_text
                        if row.get("reason"):
                            row["reason"] = redact_text(row["reason"].replace(client.api_key, "[REDACTED]"))
                        with rows_path.open("a", encoding="utf-8") as stream:
                            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                            stream.flush()
                            os.fsync(stream.fileno())
                        rows.append(row)
                print(f"completed {case_id}; reserved {len(ledger.data['attempts'])}/{MAX_CALLS}", flush=True)
            tokens = {k: sum(r.get("usage", {}).get(k, 0) or 0 for r in rows)
                      for k in ("input_tokens", "output_tokens", "cached_input_tokens")}
            uncached = tokens["input_tokens"] - tokens["cached_input_tokens"]
            report = {"protocol": frozen, "finished_at": datetime.now(timezone.utc).isoformat(),
                "reference_origin": corpus["reference_origin"], "reference_review": "not independently reviewed",
                "model_attempts": len(ledger.data["attempts"]), "tokens": tokens,
                "cost_estimate_usd": {"offpeak": (uncached * .15 + tokens['cached_input_tokens'] * .003 + tokens['output_tokens'] * .60) / 1e6,
                                      "peak": (uncached * .30 + tokens['cached_input_tokens'] * .006 + tokens['output_tokens'] * 1.20) / 1e6},
                "cost_source": "https://api-docs.deepseek.com/quick_start/pricing/",
                "summary": {m: summarize(corpus["cases"], rows, m) for m in ("deterministic", "llm")},
                "trials": rows}
            (args.out / "results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(json.dumps({"attempts": report["model_attempts"], "tokens": tokens,
                              "summary": report["summary"]}, ensure_ascii=True), flush=True)
    finally:
        os.close(lock)
        lock_path.unlink()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Avoid SDK/URL/credential exception bodies in terminal logs.
        print(f"Experiment stopped: {type(exc).__name__}", file=sys.stderr)
        sys.exit(2)
