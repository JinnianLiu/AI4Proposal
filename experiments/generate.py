"""Generate proposals for conditions C2, C3 and C4 (C1 is experiments/run_c1.py).

Every run leaves one directory, runs/exp1/gen/<cond>/<task>/r<k>/, holding
  raw.md         what the condition produced, untouched
  proposal.md    the normalized text the judges read
  task.json      the frozen task it was given
  run.json       the run record that fills the `runs` sheet

    uv run python experiments/generate.py --conditions C2 C3 C4 --tasks task_001 task_003
    uv run python experiments/generate.py --conditions C4 --max-output-tokens 32768

A run whose run.json says status=valid is skipped on re-invocation, so an
interrupted batch can simply be restarted. Void runs are retried.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

from common import (C4_DEADLINE_SECONDS, C4_MAX_OUTPUT_TOKENS, GEN_MODEL, ROOT, gen_env,
                    git_state, load_task, now_iso, read_json, run_dir, run_id, task_ids,
                    write_json)
from normalize import normalize, over_limit_sections, void_reasons
from prompts_render import render

PIPELINE = ROOT / "scripts" / "run_pipeline.py"


def _base_record(cond: str, tid: str, rep: int) -> Dict[str, Any]:
    return {"run_id": run_id(cond, tid, rep), "task_id": tid, "condition": cond,
            "rep": rep, "model": GEN_MODEL, "started_at": now_iso(), **git_state()}


def _finish(d: Path, record: Dict[str, Any], raw: str, task: Dict[str, Any],
            extra_void: Optional[list] = None, pipeline_result: Optional[dict] = None) -> Dict[str, Any]:
    (d / "raw.md").write_text(raw, encoding="utf-8")
    text, norm = normalize(raw, task)
    (d / "proposal.md").write_text(text, encoding="utf-8")
    reasons = (extra_void or []) + void_reasons(raw, pipeline_result)
    record.update({
        "finished_at": now_iso(),
        "status": "void" if reasons else "valid",
        "void_reasons": reasons,
        "char_count": len(text),
        "over_limit": over_limit_sections(text, task),
        "normalization": norm,
    })
    write_json(d / "run.json", record)
    return record


def run_pipeline_condition(cond: str, tid: str, rep: int) -> Dict[str, Any]:
    """C3 = full pipeline; C2 = --no-blueprint. C3 keeps its figure markers
    (--figures dry) for the separate figure-judge track; normalization strips
    them from what the text judges read, so the text is unaffected."""
    d = run_dir(cond, tid, rep)
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    task = load_task(tid)
    write_json(d / "task.json", task)
    record = _base_record(cond, tid, rep)
    args = [sys.executable, str(PIPELINE), "--task", str(d / "task.json"), "--out", str(d / "pipeline")]
    args += ["--figures", "dry"] if cond == "C3" else ["--figures", "off", "--no-blueprint"]
    t0 = time.time()
    with (d / "pipeline.log").open("w", encoding="utf-8") as log:
        proc = subprocess.run(args, cwd=ROOT, env=gen_env(), stdout=log, stderr=subprocess.STDOUT,
                              text=True, encoding="utf-8")
    result = read_json(d / "pipeline" / "result.json", {}) or {}
    stats = result.get("llm_stats") or {}
    record.update({"elapsed_seconds": round(time.time() - t0, 1), "exit_code": proc.returncode,
                   "llm_calls": stats.get("calls"), "failed_calls": stats.get("failed_calls"),
                   "prompt_tokens": stats.get("prompt_tokens"),
                   "completion_tokens": stats.get("completion_tokens")})
    raw_path = d / "pipeline" / "proposal_final.md"
    raw = raw_path.read_text(encoding="utf-8") if raw_path.exists() else ""
    extra = [] if proc.returncode == 0 else [f"pipeline exited with code {proc.returncode}"]
    return _finish(d, record, raw, task, extra, result)


def run_single_call(tid: str, rep: int, max_output_tokens: Optional[int] = C4_MAX_OUTPUT_TOKENS,
                    deadline_seconds: float = C4_DEADLINE_SECONDS) -> Dict[str, Any]:
    """C4: the unified prompt, one call, no tools, no continuation."""
    from ai4proposal.llm import backend_from_env, generate_with_retry
    cond = "C4"
    d = run_dir(cond, tid, rep)
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    task = load_task(tid)
    write_json(d / "task.json", task)
    system, user, _ = render(task)
    (d / "prompt.system.md").write_text(system, encoding="utf-8")
    (d / "prompt.user.md").write_text(user, encoding="utf-8")
    record = _base_record(cond, tid, rep)

    llm = backend_from_env()
    llm.model = GEN_MODEL
    llm.max_output_tokens = max_output_tokens
    # A whole document in one call can outrun the 600s per-call ceiling that suits
    # chapter-sized calls; cutting it off would truncate C4 for a reason C2/C3 never face.
    llm.deadline_seconds = deadline_seconds
    if not llm.api_key:
        sys.exit("ERROR: AI4PROPOSAL_API_KEY not set")
    t0 = time.time()
    # Retries here re-send the same single request after an API error; that is not
    # a continuation, so C4 stays one call's worth of generation.
    raw = generate_with_retry(llm, system, user)
    record.update({"elapsed_seconds": round(time.time() - t0, 1),
                   "finish_reason": llm.last_finish_reason,
                   "max_output_tokens": max_output_tokens,
                   "deadline_seconds": deadline_seconds,
                   "llm_calls": llm.stats["calls"], "failed_calls": llm.stats["failed_calls"],
                   "prompt_tokens": llm.stats["prompt_tokens"],
                   "completion_tokens": llm.stats["completion_tokens"]})
    extra = [] if raw else ["single call failed after retries"]
    return _finish(d, record, raw, task, extra)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--conditions", nargs="+", default=["C2", "C3", "C4"], choices=["C2", "C3", "C4"])
    ap.add_argument("--tasks", nargs="+", default=None, help="default: all frozen tasks")
    ap.add_argument("--rep", type=int, default=1)
    ap.add_argument("--max-output-tokens", type=int, default=C4_MAX_OUTPUT_TOKENS,
                    help="C4 only: output tokens requested (default: deepseek-flash's maximum)")
    ap.add_argument("--c4-deadline", type=float, default=C4_DEADLINE_SECONDS,
                    help="C4 only: wall-clock seconds allowed for the single call")
    ap.add_argument("--attempts", type=int, default=3, help="tries per run before it is left void")
    ap.add_argument("--force", action="store_true", help="re-run even if a valid run exists")
    args = ap.parse_args()

    for tid in args.tasks or task_ids():
        for cond in args.conditions:
            d = run_dir(cond, tid, args.rep)
            prev = read_json(d / "run.json", {}) or {}
            if prev.get("status") == "valid" and not args.force:
                print(f"skip {prev['run_id']} (valid)")
                continue
            for attempt in range(1, args.attempts + 1):
                print(f"== {run_id(cond, tid, args.rep)} attempt {attempt}", flush=True)
                rec = (run_single_call(tid, args.rep, args.max_output_tokens, args.c4_deadline) if cond == "C4"
                       else run_pipeline_condition(cond, tid, args.rep))
                rec["attempt"] = attempt
                write_json(d / "run.json", rec)
                print(f"   {rec['status']} | {rec['char_count']} chars | "
                      f"{rec.get('elapsed_seconds')}s | {rec['void_reasons'] or ''}", flush=True)
                if rec["status"] == "valid":
                    break
    return 0


if __name__ == "__main__":
    sys.exit(main())
