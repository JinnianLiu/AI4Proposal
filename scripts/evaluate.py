#!/usr/bin/env python3
"""Score a proposal Markdown document with the 7-dimension rubric panel.

Four specialist judges each read the full document and score the dimensions they
own; the chair consolidates qualitative feedback; the overall score is computed
in code from `evaluation.WEIGHTS`. No gold proposal is required.

Usage:
  # score a pipeline output (task.json is picked up from the same folder)
  python scripts/evaluate.py outputs/task_001_v8/proposal_final.md

  # explicit task, external retrieval on for the science judge
  python scripts/evaluate.py some/proposal.md --task cases/tasks/task_001.json --evidence

  # inspect the assembled rubric prompts without spending tokens
  python scripts/evaluate.py --show-rubric

Env:
  AI4PROPOSAL_API_KEY / AI4PROPOSAL_BASE_URL / AI4PROPOSAL_MODEL
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from ai4proposal.evaluation import (  # noqa: E402
    RubricPanel, WEIGHTS, DIMENSION_NAMES, RUBRICS, weighted_overall,
)


def resolve_task(md_path: Path, explicit: str) -> dict:
    """Explicit --task wins; otherwise fall back to the task.json the pipeline
    writes beside its output."""
    if explicit:
        p = Path(explicit)
        if not p.exists():
            p = ROOT / "cases" / "tasks" / (explicit if explicit.endswith(".json")
                                            else f"{explicit}.json")
        return json.loads(p.read_text(encoding="utf-8"))
    sibling = md_path.parent / "task.json"
    if sibling.exists():
        print(f"  [task] {sibling}")
        return json.loads(sibling.read_text(encoding="utf-8"))
    sys.exit("ERROR: no task found — pass --task, or run from a pipeline output dir "
             "containing task.json")


def print_report(r) -> None:
    print("\n" + "=" * 68)
    print(f"  总分  {r.overall_100} / 100   ({r.overall_score}/10)    判定: {r.verdict}")
    print("=" * 68)
    print(f"\n{'维度':<22}{'权重':>8}{'得分':>8}")
    print("-" * 68)
    for dim in WEIGHTS:
        score = r.scores.get(dim)
        shown = f"{score:.0f}/10" if score is not None else "FAILED"
        print(f"{DIMENSION_NAMES[dim]:<20}{WEIGHTS[dim]:>8.2f}{shown:>10}")
    print("-" * 68)

    for label, items in (("优点", r.strengths), ("问题", r.weaknesses)):
        if items:
            print(f"\n## {label}")
            for x in items:
                print(f"  - {x}")
    if r.summary:
        print(f"\n## 总体评语\n  {r.summary}")

    flags = [
        ("过度声称", r.over_claims),
        ("跑题内容", r.off_topic),
        ("存疑指标", r.risky_targets),
        ("占位符残留", r.placeholders),
        ("违反约束", r.constraint_violations),
    ]
    shown = [(k, v) for k, v in flags if v]
    if shown:
        print("\n## 硬性问题")
        for k, v in shown:
            print(f"  [{k}]")
            for x in v:
                print(f"    - {x}")

    uncovered = [c for c in r.requirement_coverage if not c.get("covered", True)]
    missing = [s for s in r.section_checklist if not s.get("present", True)]
    if uncovered:
        print("\n## 未落实的指南要求")
        for c in uncovered:
            print(f"  - {c.get('requirement', '')}")
    if missing:
        print("\n## 缺失章节")
        for s in missing:
            print(f"  - {s.get('section', '')}: {s.get('note', '')}")
    if r.errors:
        print("\n## 评审过程错误")
        for e in r.errors:
            print(f"  ! {e}")


def show_rubric() -> None:
    total = sum(WEIGHTS.values())
    print(f"7-dimension rubric — weights sum to {total:.2f}\n")
    for dim, w in WEIGHTS.items():
        print(f"{'=' * 68}\n{DIMENSION_NAMES[dim]}  ({dim})   weight {w:.2f}\n{'=' * 68}")
        print(RUBRICS[dim] + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("proposal", nargs="?", default="", help="proposal Markdown file")
    ap.add_argument("--task", default="", help="task json path or id (default: task.json beside the md)")
    ap.add_argument("--evidence", action="store_true", help="retrieve external literature for the science judge")
    ap.add_argument("--out", default="", help="write full result JSON here (default: evaluation.json beside the md)")
    ap.add_argument("--show-rubric", action="store_true", help="print the rubrics and exit (no API calls)")
    args = ap.parse_args()

    if args.show_rubric:
        show_rubric()
        return 0
    if not args.proposal:
        ap.error("proposal markdown path is required (or use --show-rubric)")

    md_path = Path(args.proposal)
    if not md_path.exists():
        sys.exit(f"ERROR: no such file: {md_path}")
    proposal_text = md_path.read_text(encoding="utf-8")
    task = resolve_task(md_path, args.task)

    import os
    from ai4proposal.llm import LLMBackend
    llm = LLMBackend(
        model=os.environ.get("AI4PROPOSAL_MODEL", "deepseek-chat"),
        api_key=os.environ.get("AI4PROPOSAL_API_KEY", ""),
        base_url=os.environ.get("AI4PROPOSAL_BASE_URL", "https://api.deepseek.com"),
        timeout_seconds=float(os.environ.get("AI4PROPOSAL_TIMEOUT_SECONDS", "180")),
    )
    if not llm.api_key:
        sys.exit("ERROR: AI4PROPOSAL_API_KEY not set")

    print(f"== 评审 {md_path}  ({len(proposal_text)}字)")
    print(f"   {task.get('title', '')}")
    print(f"   model: {llm.model} | judges: 4 + chair | evidence: {'on' if args.evidence else 'off'}")

    pack = None
    if args.evidence:
        from ai4proposal.evidence import gather_evidence
        from ai4proposal.llm import cheap_backend
        pack = gather_evidence(llm, proposal_text, verbose=True,
                               rerank_llm=cheap_backend(llm))

    result = RubricPanel(llm, verbose=True).evaluate(proposal_text, task, evidence=pack)
    print_report(result)

    out = Path(args.out) if args.out else md_path.parent / "evaluation.json"
    json.dump(result.to_dict(), out.open("w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print(f"\n-> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
