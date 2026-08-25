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
import re
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


_IMG_TAG = re.compile(r"!\[[^\]]*\]\(([^)]*?figures/([^)/]+\.png))\)")


def collect_figures(md_path: Path, proposal_text: str) -> list:
    """Every rendered figure in the document, with the caption it carries and the
    body section it sits in.

    The section is what the figure is judged against — "does this picture show
    what this part of the text is arguing" needs the text, not just the caption.
    Captions come from the document itself rather than figure_manifest.json, so
    a hand-edited caption is the one reviewed.
    """
    heads = [(m.start(), m.group(1).strip())
             for m in re.finditer(r"^##\s+(.+?)\s*$", proposal_text, re.M)]

    def section_at(pos: int) -> tuple:
        start, name = 0, ""
        for s, n in heads:
            if s <= pos:
                start, name = s, n
            else:
                return name, proposal_text[start:s]
        return name, proposal_text[start:]

    figures = []
    for m in _IMG_TAG.finditer(proposal_text):
        path = (md_path.parent / m.group(1)).resolve()
        if not path.exists():
            print(f"  [figure] 跳过：文件不存在 {m.group(1)}")
            continue
        # The caption is the emphasised line the pipeline writes right after the
        # image ("*图1：…*" / "*Figure 1. …*").
        tail = proposal_text[m.end():m.end() + 400]
        cap = re.search(r"\*([^*\n]+)\*", tail)
        name, body = section_at(m.start())
        figures.append({
            "id": m.group(2).rsplit(".", 1)[0],
            "rel": m.group(1),
            "path": str(path),
            "caption": cap.group(1).strip() if cap else "",
            "section_name": name,
            "section_text": body,
        })
    return figures


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

    if r.research_type:
        print(f"\n## 评分口径\n  按【{r.research_type.get('type', '?')}】评："
              f"{r.research_type.get('reason', '')}")
    sc = r.schedule_check
    if sc and sc.get("consistent") is False:
        print(f"\n## 周期不符\n  指南规定 {sc.get('required', '?')}，"
              f"正文规划 {sc.get('planned', '?')}")
    no_path = [x for x in r.requirement_feasibility if not x.get("has_path", True)]
    if no_path:
        print("\n## 硬性交付缺少落实路径")
        for x in no_path:
            print(f"  - {x.get('requirement', '')}")

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
    if r.figure_findings:
        # Findings only — this judge carries no weight and no gate, so the
        # report has to make its findings visible on their own.
        marks = {"good": "OK", "acceptable": "尚可", "partial": "部分", "mismatch": "不符", "poor": "差"}
        print("\n## 配图审查（不计入总分）")
        for f in r.figure_findings:
            match, qual = f.get("match") or {}, f.get("quality") or {}
            mv, qv = match.get("verdict", "?"), qual.get("verdict", "?")
            print(f"\n  [{f.get('figure_id', '?')}] 与正文匹配度: {marks.get(mv, mv)} | "
                  f"图片表现: {marks.get(qv, qv)}")
            if match.get("reason"):
                print(f"    匹配度依据：{match['reason']}")
            if qual.get("reason"):
                print(f"    表现依据：{qual['reason']}")
            for x in f.get("issues") or []:
                print(f"    - 问题：{x}")
            for x in f.get("fabricated_numbers") or []:
                print(f"    - 图中数值：{x}")
            if f.get("suggestion"):
                print(f"    改进：{f['suggestion']}")

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
    ap.add_argument("--figures", choices=["auto", "on", "off"], default="auto",
                    help="multimodal figure review: auto = run when the document has rendered figures")
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
        timeout_seconds=float(os.environ.get("AI4PROPOSAL_TIMEOUT_SECONDS", "300")),
        max_retries=int(os.environ.get("AI4PROPOSAL_SDK_RETRIES", "1")),
        deadline_seconds=float(os.environ.get("AI4PROPOSAL_CALL_DEADLINE_SECONDS", "600")),
    )
    if not llm.api_key:
        sys.exit("ERROR: AI4PROPOSAL_API_KEY not set")

    figures, vision = [], None
    if args.figures != "off":
        figures = collect_figures(md_path, proposal_text)
        if figures:
            from ai4proposal.llm import vision_backend
            try:
                vision = vision_backend(llm)
            except ValueError as e:
                print(f"  [figure] 跳过配图审查：{e}")
                figures = []
        elif args.figures == "on":
            print("  [figure] 文档中没有已渲染的配图，跳过配图审查")

    print(f"== 评审 {md_path}  ({len(proposal_text)}字)")
    print(f"   {task.get('title', '')}")
    print(f"   model: {llm.model} | judges: 4 + chair | evidence: {'on' if args.evidence else 'off'}"
          + (f" | 配图: {len(figures)} 张 ({vision.model})" if figures else " | 配图: 无"))

    pack = None
    if args.evidence:
        from ai4proposal.evidence import gather_evidence
        from ai4proposal.llm import cheap_backend
        pack = gather_evidence(llm, proposal_text, verbose=True,
                               rerank_llm=cheap_backend(llm))

    result = RubricPanel(llm, verbose=True).evaluate(
        proposal_text, task, evidence=pack, figures=figures, vision_llm=vision)
    print_report(result)

    out = Path(args.out) if args.out else md_path.parent / "evaluation.json"
    json.dump(result.to_dict(), out.open("w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print(f"\n-> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
