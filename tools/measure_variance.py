#!/usr/bin/env python3
"""Measure run-to-run variance of the rubric panel on one document.

Scores the same document N times and reports per-dimension mean / sd / range.
Any comparison between two proposals is meaningless unless the gap exceeds this
noise floor, so run this before reading anything into a score difference.

Evidence, when enabled, is retrieved ONCE and reused across every run, so what
is measured is judge variance rather than retrieval variance.

  python scripts/measure_variance.py outputs/task_001_v8/proposal_final.md --runs 5
  python scripts/measure_variance.py <md> --task <t> --runs 5 --evidence
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from ai4proposal.evaluation import RubricPanel, WEIGHTS, DIMENSION_NAMES  # noqa: E402
from ai4proposal.llm import LLMBackend  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("proposal")
    ap.add_argument("--task", default="")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--evidence", action="store_true")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    md = Path(args.proposal)
    text = md.read_text(encoding="utf-8")
    task_path = Path(args.task) if args.task else md.parent / "task.json"
    task = json.loads(task_path.read_text(encoding="utf-8"))

    llm = LLMBackend(
        model=os.environ.get("AI4PROPOSAL_MODEL", "deepseek-chat"),
        api_key=os.environ.get("AI4PROPOSAL_API_KEY", ""),
        base_url=os.environ.get("AI4PROPOSAL_BASE_URL", "https://api.deepseek.com"),
        timeout_seconds=float(os.environ.get("AI4PROPOSAL_TIMEOUT_SECONDS", "180")),
    )
    if not llm.api_key:
        sys.exit("ERROR: AI4PROPOSAL_API_KEY not set")

    # long runs are usually redirected to a file; unbuffered output keeps them
    # observable instead of silent until exit
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass

    print(f"== {md}  ×{args.runs} 次  ({task.get('title','')[:40]})")
    pack = None
    if args.evidence:
        from ai4proposal.evidence import gather_evidence
        from ai4proposal.llm import cheap_backend
        print("  检索证据（一次，全部轮次复用）…")
        pack = gather_evidence(llm, text, verbose=True, rerank_llm=cheap_backend(llm))

    panel = RubricPanel(llm)
    runs = []
    for i in range(args.runs):
        r = panel.evaluate(text, task, evidence=pack)
        runs.append(r)
        dims = " ".join(f"{d[:4]}={r.scores.get(d, '-')}" for d in WEIGHTS)
        print(f"  [{i+1}/{args.runs}] {r.overall_100:>5} / 100  {r.verdict:<18} {dims}")
        if r.errors:
            print(f"        errors: {r.errors}")

    print(f"\n{'维度':<16}{'均值':>8}{'标准差':>9}{'最低':>7}{'最高':>7}{'极差':>7}")
    print("-" * 56)
    noisy = []
    for d in WEIGHTS:
        vals = [r.scores[d] for r in runs if d in r.scores]
        if not vals:
            print(f"{DIMENSION_NAMES[d]:<14}{'全部失败':>10}")
            continue
        sd = statistics.stdev(vals) if len(vals) > 1 else 0.0
        rng = max(vals) - min(vals)
        print(f"{DIMENSION_NAMES[d]:<14}{statistics.mean(vals):>9.2f}{sd:>9.2f}"
              f"{min(vals):>7.0f}{max(vals):>7.0f}{rng:>7.0f}")
        if rng >= 3:
            noisy.append((DIMENSION_NAMES[d], rng))
    print("-" * 56)

    totals = [r.overall_100 for r in runs]
    sd = statistics.stdev(totals) if len(totals) > 1 else 0.0
    print(f"{'总分':<14}{statistics.mean(totals):>9.2f}{sd:>9.2f}"
          f"{min(totals):>7.1f}{max(totals):>7.1f}{max(totals)-min(totals):>7.1f}")

    verdicts = {}
    for r in runs:
        verdicts[r.verdict] = verdicts.get(r.verdict, 0) + 1
    print(f"\n判定分布: {verdicts}")
    print(f"\n>> 噪声底线：两份本子总分相差不到 {2*sd:.1f} 分（2σ）时，不能认为有差异。")
    if noisy:
        print(">> 极差 ≥3 分的维度（单次结果不可信）: "
              + ", ".join(f"{n}({r:.0f})" for n, r in noisy))

    if args.out:
        json.dump({"runs": [r.to_dict() for r in runs],
                   "totals": totals,
                   "sd_overall": sd},
                  open(args.out, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
        print(f"\n-> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
