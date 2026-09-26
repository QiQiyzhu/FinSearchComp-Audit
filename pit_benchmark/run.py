"""Plan offline by default; explicitly execute or replay the frozen 36-call pilot.

python -m pit_benchmark.run --output build/pit-plan.json
python -m pit_benchmark.run --execute --output docs/verification/pit-pilot-live.json
python -m pit_benchmark.run --replay docs/verification/pit-pilot-live.trace.jsonl --output build/pit-replay.json
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import time

from .pilot import (HERE, MAX_CALLS, canonical, load_fixture, now, parse_answer, plan_bundle,
                    request_payload, score_answer, sha, summarize)


def code_hashes() -> dict:
    return {name: sha((HERE / name).read_bytes().replace(b"\r\n", b"\n"))
            for name in ("pilot.py", "run.py")}


def append_event(handle, event: dict) -> None:
    handle.write(canonical(event) + "\n")
    handle.flush()
    os.fsync(handle.fileno())


def apply_response(bundle: dict, fixture: dict, run: dict, event: dict) -> None:
    run["evaluated_at"] = event["evaluated_at"]
    run["latency_ms"] = event["latency_ms"]
    run["provider"] = deepcopy(event["provider"])
    run["response_sha256"] = event.get("response_sha256")
    if event.get("error"):
        run.update(status="error", error=event["error"])
        return
    try:
        if sha(event["response_text"].encode("utf-8")) != event["response_sha256"]:
            raise ValueError("Response SHA mismatch")
        response = json.loads(event["response_text"])
        if not isinstance(response, dict):
            raise ValueError("Provider response must be an object")
        usage = response.get("usage") or {}
        if not isinstance(usage, dict) or any(
            not isinstance(usage[key], int) or isinstance(usage[key], bool) or usage[key] < 0
            for key in ("total_tokens", "prompt_tokens", "completion_tokens") if key in usage
        ):
            raise ValueError("Invalid provider usage metadata")
        run["usage"] = usage
        run["provider"].update(response_model=response.get("model"), response_id=response.get("id"))
        if not run["provider"].get("request_id"):
            run["provider"]["request_id"] = response.get("id")
        choice = response["choices"][0]
        if not isinstance(choice, dict):
            raise ValueError("Provider choice must be an object")
        run["provider"]["finish_reason"] = choice.get("finish_reason")
        if choice.get("finish_reason") != "stop":
            raise ValueError("Response did not finish normally")
        answer = parse_answer(choice["message"]["content"])
        case = next(case for case in fixture["cases"] if case["id"] == run["case_id"])
        score = score_answer(fixture, case, run["phase"], run["condition"], answer)
        run.update(status="completed", answer=answer, score=score)
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        run.update(status="invalid", error={"code": "INVALID_RESPONSE", "detail": str(exc)[:200]})


def execute(bundle: dict, fixture: dict, settings, trace_path: Path, *, max_calls: int = MAX_CALLS) -> dict:
    """One HTTP request per unit, with append-only write-ahead request recording."""
    import httpx
    if not 1 <= max_calls <= MAX_CALLS:
        raise ValueError("Call cap must be between 1 and 36")
    if not settings.deepseek_api_key:
        raise ValueError("DEEPSEEK_API_KEY is not configured")
    if not settings.deepseek_base_url.startswith("https://"):
        raise ValueError("Provider endpoint must use HTTPS")
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    started = now()
    endpoint = settings.deepseek_base_url.rstrip("/") + "/chat/completions"
    model = settings.deepseek_model
    bundle.update(mode="live", started_at=started,
                  execution={"requested_model": model, "endpoint": endpoint, "max_calls": max_calls,
                             "temperature": 0, "max_tokens": 700, "max_retries": 0,
                             "trust_env": False, "code_sha256": code_hashes()})
    cases = {case["id"]: case for case in fixture["cases"]}
    # transport has no retries, and the loop never retries a failed unit.
    with trace_path.open("x", encoding="utf-8", newline="\n") as trace:
        append_event(trace, {"event": "header", "recorded_at": started,
                            "dataset_sha256": bundle["dataset_sha256"],
                            "protocol": bundle["protocol"], "execution": bundle["execution"]})
        with httpx.Client(timeout=httpx.Timeout(90, connect=15), trust_env=False,
                          follow_redirects=False, transport=httpx.HTTPTransport(retries=0)) as client:
            for run in bundle["runs"][:max_calls]:
                payload = request_payload(fixture, cases[run["case_id"]], run["phase"], run["condition"], model)
                request_sha = sha(canonical(payload).encode("utf-8"))
                run["request_sha256"] = request_sha
                append_event(trace, {"event": "request", "run_id": run["id"], "requested_at": now(),
                                     "request_sha256": request_sha, "request": payload})
                event = {"event": "response", "run_id": run["id"],
                         "provider": {"requested_model": model, "endpoint": endpoint,
                                      "response_model": None, "response_id": None,
                                      "request_id": None, "finish_reason": None},
                         "response_text": None, "response_sha256": None, "error": None}
                clock = time.monotonic()
                try:
                    response = client.post(endpoint, json=payload,
                                           headers={"Authorization": "Bearer " + settings.deepseek_api_key})
                    # Keys are never written. Provider response bodies are retained, with
                    # an exact configured-secret replacement should a provider echo it.
                    response_text = response.text.replace(settings.deepseek_api_key, "[REDACTED_API_KEY]")
                    event.update(http_status=response.status_code, response_text=response_text,
                                 response_sha256=sha(response_text.encode("utf-8")))
                    event["provider"]["request_id"] = response.headers.get("x-request-id") or response.headers.get("x-ds-request-id")
                    if response.status_code != 200:
                        event["error"] = {"code": "HTTP_ERROR", "http_status": response.status_code}
                except httpx.HTTPError as exc:
                    event["error"] = {"code": "TRANSPORT_ERROR", "exception_type": type(exc).__name__}
                event.update(evaluated_at=now(), latency_ms=round((time.monotonic() - clock) * 1000))
                append_event(trace, event)
                apply_response(bundle, fixture, run, event)
                print(f"{run['id']} {run['status']}", flush=True)
                if event.get("http_status") in {401, 402, 403, 429}:
                    # Avoid repeating authorization, balance, or rate-limit failures.
                    bundle["stopped_reason"] = f"Provider HTTP {event['http_status']}"
                    break
        bundle["completed_at"] = now()
        bundle["summary"] = summarize(bundle["runs"])
        append_event(trace, {"event": "footer", "completed_at": bundle["completed_at"],
                            "summary": bundle["summary"], "stopped_reason": bundle.get("stopped_reason")})
    bundle["generated_at"] = now()
    bundle["trace_sha256"] = sha(trace_path.read_bytes())
    bundle["trace_file"] = trace_path.name
    return bundle


def replay(trace_path: Path, fixture: dict, manifest: dict, audit: dict) -> dict:
    """Reconstruct every score from frozen questions and the saved raw responses."""
    events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not events or events[0].get("event") != "header":
        raise ValueError("Trace header missing")
    header = events[0]
    if header["dataset_sha256"] != manifest["dataset_sha256"] or header["protocol"] != fixture["protocol"]:
        raise ValueError("Trace and frozen dataset/protocol differ")
    if header["execution"]["code_sha256"] != code_hashes():
        raise ValueError("Replay needs the recorded runner/scorer source revision")
    if not 1 <= header["execution"]["max_calls"] <= MAX_CALLS:
        raise ValueError("Invalid recorded call cap")
    bundle = plan_bundle(fixture, manifest, audit)
    bundle.update(mode="live", started_at=header["recorded_at"], execution=header["execution"],
                  replayed_at=now(), trace_file=trace_path.name, trace_sha256=sha(trace_path.read_bytes()))
    runs = {run["id"]: run for run in bundle["runs"]}
    cases = {case["id"]: case for case in fixture["cases"]}
    requested, responded = set(), set()
    footer = None
    for event in events[1:]:
        if footer is not None:
            raise ValueError("Trace has events after footer")
        if event["event"] == "footer":
            footer = event
            continue
        identifier = event["run_id"]
        if identifier not in runs:
            raise ValueError("Unknown run ID")
        run = runs[identifier]
        if event["event"] == "request":
            if identifier in requested or len(requested) >= header["execution"]["max_calls"]:
                raise ValueError("Repeated unit or call cap exceeded")
            expected = request_payload(fixture, cases[run["case_id"]], run["phase"], run["condition"],
                                       header["execution"]["requested_model"])
            expected_sha = sha(canonical(expected).encode("utf-8"))
            if event["request"] != expected or event["request_sha256"] != expected_sha:
                raise ValueError("Request differs from the frozen protocol")
            requested.add(identifier)
            run["request_sha256"] = expected_sha
        elif event["event"] == "response":
            if identifier not in requested or identifier in responded:
                raise ValueError("Response missing request or repeated")
            if event.get("response_text") is not None and sha(event["response_text"].encode("utf-8")) != event.get("response_sha256"):
                raise ValueError("Raw response SHA mismatch")
            responded.add(identifier)
            apply_response(bundle, fixture, run, event)
        else:
            raise ValueError("Unknown event type")
    for identifier in requested - responded:
        runs[identifier].update(status="error", error={"code": "INTERRUPTED_NO_RESPONSE"})
    bundle["summary"] = summarize(bundle["runs"])
    if footer:
        if footer["summary"] != bundle["summary"]:
            raise ValueError("Recorded summary differs from raw-response replay")
        bundle["completed_at"] = footer["completed_at"]
        if footer.get("stopped_reason"):
            bundle["stopped_reason"] = footer["stopped_reason"]
    else:
        bundle["stopped_reason"] = "Incomplete trace; missing footer"
    return bundle


def write_exclusive(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true", help="Execute at most 36 potentially billed requests; no retries")
    mode.add_argument("--replay", type=Path, help="Re-score saved request/response trace without network or API key")
    parser.add_argument("--output", type=Path, required=True, help="New receipt path; existing files are never overwritten")
    parser.add_argument("--trace", type=Path, help="New append-only trace path; defaults beside output")
    parser.add_argument("--max-calls", type=int, default=MAX_CALLS)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists; choose a new filename")
    if not 1 <= args.max_calls <= MAX_CALLS:
        parser.error("--max-calls must be between 1 and 36")
    fixture, manifest, audit = load_fixture()
    bundle = plan_bundle(fixture, manifest, audit)
    if args.replay:
        bundle = replay(args.replay, fixture, manifest, audit)
    elif args.execute:
        from research_workbench.config import Settings
        trace_path = args.trace or args.output.with_suffix(".trace.jsonl")
        if trace_path.exists() or trace_path.resolve() == args.output.resolve():
            parser.error("Trace must be a distinct new file")
        bundle = execute(bundle, fixture, Settings.from_env(), trace_path, max_calls=args.max_calls)
    write_exclusive(args.output, bundle)
    print(json.dumps({"mode": bundle["mode"], "dataset_sha256": bundle["dataset_sha256"],
                      "summary": bundle["summary"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
