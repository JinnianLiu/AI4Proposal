"""One run end to end: generate one proposal for one condition and task, judge
it, and print the result. For trying a condition or a task by itself; batches
go through generate.py / run_c1.py / judge.py.

    uv run python experiments/run_one.py --condition C4 --task task_001
    uv run python experiments/run_one.py --condition C3 --task task_008 --judges deepseek gemini
    uv run python experiments/run_one.py --condition C1-CC --task task_001 --no-judge
    uv run python experiments/run_one.py --condition C4 --task task_001 --force   # redo both steps

It writes to the same places as the batch scripts (runs/exp1/gen/..., runs/exp1/judge/...),
so a run made here counts in the experiment like any other. An existing valid
generation or evaluation is reused unless --force is given.
"""
from __future__ import annotations

import argparse
import sys
from typing import Any, Dict, List

from common import (C4_DEADLINE_SECONDS, C4_MAX_OUTPUT_TOKENS, CONDITIONS, read_json, run_dir,
                    task_ids, write_json)


def generate(cond: str, tid: str, rep: int, args: argparse.Namespace) -> Dict[str, Any]:
    if cond == "C4":
        from generate import run_single_call
        rec = run_single_call(tid, rep, args.max_output_tokens, args.c4_deadline)
    elif cond in ("C2", "C3"):
        from generate import run_pipeline_condition
        rec = run_pipeline_condition(cond, tid, rep)
    else:
        from run_c1 import run_one as run_c1_one
        rec = run_c1_one(cond.split("-")[1], tid, rep)
    rec["attempt"] = 1
    write_json(run_dir(cond, tid, rep) / "run.json", rec)
    return rec


def judge(rec: Dict[str, Any], names: List[str], reps: List[int], force: bool) -> List[Dict[str, Any]]:
    from judge import evaluate_one, load_judges, out_path
    run = dict(rec, _dir=run_dir(rec["condition"], rec["task_id"], rec["rep"]))
    out = []
    for j in load_judges(names or None):
        for jrep in reps:
            status = evaluate_one(j, run, jrep, force=force)
            out.append({"judge": j["name"], "judge_rep": jrep, "status": status,
                        **(read_json(out_path(j["name"], run, jrep), {}) or {})})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate and judge a single run.")
    ap.add_argument("--condition", required=True, choices=CONDITIONS)
    ap.add_argument("--task", required=True, choices=task_ids())
    ap.add_argument("--rep", type=int, default=1, help="generation repetition number (default 1)")
    ap.add_argument("--judges", nargs="*", default=[],
                    help="judge names from judges.json (default: all of them)")
    ap.add_argument("--judge-reps", nargs="+", type=int, default=[1])
    ap.add_argument("--no-judge", action="store_true", help="generate only")
    ap.add_argument("--force", action="store_true", help="regenerate and rejudge even if valid results exist")
    ap.add_argument("--max-output-tokens", type=int, default=C4_MAX_OUTPUT_TOKENS, help="C4 only")
    ap.add_argument("--c4-deadline", type=float, default=C4_DEADLINE_SECONDS, help="C4 only, seconds")
    args = ap.parse_args()

    cond, tid, rep = args.condition, args.task, args.rep
    d = run_dir(cond, tid, rep)
    rec = read_json(d / "run.json", {}) or {}
    if rec.get("status") == "valid" and not args.force:
        print(f"[generate] reuse existing valid run {rec['run_id']}  (--force to regenerate)")
    else:
        print(f"[generate] {cond} / {tid} / r{rep} ...", flush=True)
        rec = generate(cond, tid, rep, args)

    print(f"\n  run_id        {rec.get('run_id')}")
    print(f"  status        {rec.get('status')}  {'; '.join(rec.get('void_reasons') or [])}")
    print(f"  chars         {rec.get('char_count')}")
    print(f"  elapsed       {rec.get('elapsed_seconds')} s")
    print(f"  llm calls     {rec.get('llm_calls')}  tokens in/out {rec.get('prompt_tokens')}/{rec.get('completion_tokens')}")
    if rec.get("finish_reason"):
        print(f"  finish_reason {rec['finish_reason']}")
    if rec.get("framework"):
        print(f"  framework     {rec['framework']}  turns {rec.get('turns')}  continuations {rec.get('continuations')}")
    missing = (rec.get("normalization") or {}).get("missing_sections") or []
    if missing:
        print(f"  missing       {missing}")
    print(f"  proposal      {d / 'proposal.md'}")

    if args.no_judge:
        return 0 if rec.get("status") == "valid" else 1
    if rec.get("status") != "valid":
        print("\n[judge] skipped: the run is void")
        return 1

    print("\n[judge] ...", flush=True)
    results = judge(rec, args.judges, args.judge_reps, args.force)
    for r in results:
        res = r.get("result") or {}
        if r.get("status") in ("valid", "skip") and res:
            scores = " ".join(f"{k[:4]}={v:g}" for k, v in (res.get("scores") or {}).items())
            print(f"  {r['judge']:<9} j{r['judge_rep']}  {res.get('overall_100'):>5}  "
                  f"{res.get('verdict'):<17} {scores}")
        else:
            print(f"  {r['judge']:<9} j{r['judge_rep']}  {r.get('status')}")
    return 0 if all(r.get("status") in ("valid", "skip") for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
