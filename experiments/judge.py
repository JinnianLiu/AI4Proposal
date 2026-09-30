"""Score every valid generated proposal with each judge model (PLAN §5).

Each (run, judge, judge-rep) is one full RubricPanel — four specialists and the
chair — on the normalized proposal.md, with no retrieval, no figures and no
truncation. An evaluation counts only if every judge call succeeded and all
seven dimensions were scored; otherwise it is retried, and after three failures
recorded as missing. It is never scored with a fallback value or with a
dimension's weight quietly redistributed (PLAN §3.5 item 3).

    uv run python experiments/judge.py                          # every judge, rep 1
    uv run python experiments/judge.py --judges gemini --reps 1 2 --tasks task_001
    uv run python experiments/judge.py --check                  # connectivity only
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional

from common import EXP, RUNS, now_iso, read_json, write_json

os.environ.setdefault("AI4PROPOSAL_QUIET_LLM", "1")   # threads would interleave per-call traces

from ai4proposal.evaluation import WEIGHTS, RubricPanel  # noqa: E402
from ai4proposal.llm import LLMBackend  # noqa: E402

ATTEMPTS = 3
_print_lock = threading.Lock()


def log(msg: str) -> None:
    with _print_lock:
        print(msg, flush=True)


def load_judges(names: Optional[List[str]]) -> List[Dict[str, Any]]:
    judges = read_json(EXP / "judges.json", {})["judges"]
    if names:
        judges = [j for j in judges if j["name"] in names]
    return judges


def backend_for(judge: Dict[str, Any]) -> LLMBackend:
    key = os.environ.get(judge["api_key_env"], "")
    if not key:
        raise RuntimeError(f"{judge['name']}: environment variable {judge['api_key_env']} is not set")
    return LLMBackend(
        model=judge["model"], api_key=key, base_url=judge["base_url"],
        timeout_seconds=float(os.environ.get("AI4PROPOSAL_TIMEOUT_SECONDS", "300")),
        max_retries=int(os.environ.get("AI4PROPOSAL_SDK_RETRIES", "1")),
        deadline_seconds=float(os.environ.get("AI4PROPOSAL_CALL_DEADLINE_SECONDS", "600")),
    )


def valid_runs(conditions: Optional[List[str]], tasks: Optional[List[str]]) -> List[Dict[str, Any]]:
    runs = []
    for p in sorted((RUNS / "gen").glob("*/*/r*/run.json")):
        rec = read_json(p, {}) or {}
        if rec.get("status") != "valid":
            continue
        if conditions and rec["condition"] not in conditions:
            continue
        if tasks and rec["task_id"] not in tasks:
            continue
        rec["_dir"] = p.parent
        runs.append(rec)
    return runs


def out_path(judge: str, run: Dict[str, Any], jrep: int) -> Path:
    return (RUNS / "judge" / judge / run["condition"] / run["task_id"]
            / f"r{run['rep']}_j{jrep}.json")


def is_complete(result: Dict[str, Any]) -> bool:
    return not result.get("errors") and set(result.get("scores") or {}) >= set(WEIGHTS)


def evaluate_one(judge: Dict[str, Any], run: Dict[str, Any], jrep: int, force: bool = False) -> str:
    path = out_path(judge["name"], run, jrep)
    if not force and (read_json(path, {}) or {}).get("status") == "valid":
        return "skip"
    text = (run["_dir"] / "proposal.md").read_text(encoding="utf-8")
    task = read_json(run["_dir"] / "task.json")
    attempts: List[Dict[str, Any]] = []
    for attempt in range(1, ATTEMPTS + 1):
        llm = backend_for(judge)
        try:
            result = RubricPanel(llm, max_proposal_tokens=None).evaluate(text, task).to_dict()
        except Exception as exc:          # noqa: BLE001 - recorded as a failed attempt
            result = {"errors": [f"{type(exc).__name__}: {exc}"], "scores": {}}
        record = {"run_id": run["run_id"], "condition": run["condition"], "task_id": run["task_id"],
                  "gen_rep": run["rep"], "judge": judge["name"], "judge_model": judge["model"],
                  "judge_rep": jrep, "attempt": attempt, "evaluated_at": now_iso(),
                  "llm_stats": dict(llm.stats), "result": result}
        if is_complete(result):
            record["status"] = "valid"
            record["failed_attempts"] = attempts
            write_json(path, record)
            log(f"  ok   {judge['name']:<8} {run['run_id']} j{jrep}  {result['overall_100']}  {result['verdict']}")
            return "valid"
        attempts.append({"attempt": attempt, "errors": result.get("errors")})
        log(f"  fail {judge['name']:<8} {run['run_id']} j{jrep} attempt {attempt}: "
            f"{'; '.join(str(e) for e in result.get('errors') or [])[:160]}")
    write_json(path, {"run_id": run["run_id"], "condition": run["condition"], "task_id": run["task_id"],
                      "gen_rep": run["rep"], "judge": judge["name"], "judge_model": judge["model"],
                      "judge_rep": jrep, "status": "missing", "failed_attempts": attempts,
                      "evaluated_at": now_iso()})
    return "missing"


def check(judges: List[Dict[str, Any]]) -> int:
    """One tiny call per judge: keys, endpoints and model ids, before a batch."""
    bad = 0
    for j in judges:
        try:
            reply = backend_for(j).generate_text("只输出 JSON。", '输出 {"ok": true}')
            log(f"  {j['name']:<8} {j['model']}: {reply[:60]!r}")
        except Exception as exc:          # noqa: BLE001
            bad += 1
            log(f"  {j['name']:<8} {j['model']}: FAILED {type(exc).__name__}: {str(exc)[:160]}")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--judges", nargs="+", default=None, help="names from judges.json (default: all)")
    ap.add_argument("--conditions", nargs="+", default=None)
    ap.add_argument("--tasks", nargs="+", default=None)
    ap.add_argument("--reps", nargs="+", type=int, default=[1], help="judge repetitions to produce")
    ap.add_argument("--workers", type=int, default=4, help="concurrent panels")
    ap.add_argument("--check", action="store_true", help="test each judge endpoint and exit")
    args = ap.parse_args()

    judges = load_judges(args.judges)
    if args.check:
        return check(judges)
    runs = valid_runs(args.conditions, args.tasks)
    jobs = [(j, r, rep) for j in judges for r in runs for rep in args.reps]
    log(f"{len(runs)} valid runs x {len(judges)} judges x reps {args.reps} = {len(jobs)} panels")
    tally: Dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for fut in as_completed([pool.submit(evaluate_one, *job) for job in jobs]):
            try:
                status = fut.result()
            except Exception as exc:      # noqa: BLE001 - e.g. a missing key
                status = "error"
                log(f"  error: {exc}")
            tally[status] = tally.get(status, 0) + 1
    log(f"done: {tally}")
    return 0 if not tally.get("missing") and not tally.get("error") else 1


if __name__ == "__main__":
    sys.exit(main())
