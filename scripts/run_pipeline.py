#!/usr/bin/env python3
"""Structure-driven proposal pipeline for a guideline-grounded task.

Flow:
  Step 0  -> blueprint (thesis / deliverables / key_methods / novelty_angles)
  for each section in task.structure.core_sections:
      general-rules + generic-writer + auto format-modifiers -> draft (一次成稿)
  assemble -> generate REAL figures -> (optional) panel judge (reads the task directly)

Prompts live in ai4proposal.writer_prompts (domain-neutral; substance via task).

Env (no keys in source):
  AI4PROPOSAL_API_KEY / AI4PROPOSAL_BASE_URL / AI4PROPOSAL_MODEL   text LLM
  AI4PROPOSAL_IMAGE_API_KEY / AI4PROPOSAL_IMAGE_BASE_URL / AI4PROPOSAL_IMAGE_MODEL

Example (PowerShell):
  $env:PYTHONUTF8=1
  $env:AI4PROPOSAL_API_KEY="sk-..."; $env:AI4PROPOSAL_BASE_URL="https://api.deepseek.com"; $env:AI4PROPOSAL_MODEL="deepseek-chat"
  $env:AI4PROPOSAL_IMAGE_API_KEY="sk-..."
  python scripts/run_pipeline.py --task cases/tasks/task_001.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from ai4proposal.llm import backend_from_env, generate_with_retry, parse_json
from ai4proposal.image_gen import plan_figure, render_figure, image_config_from_env, write_plan_record
from ai4proposal import writer_prompts as WP

# Fallback structure for tasks that don't declare one (keeps pipeline general).
DEFAULT_STRUCTURE = {
    "template": "科研项目申请书",
    "core_sections": [
        {"id": "objectives", "name": "研究目标", "required": ["面向的问题与需求", "总体研究框架", "3-4个具体目标"]},
        {"id": "kpi", "name": "考核指标", "required": ["按成果逐条列出", "每条量化(带数值)", "考核方式及评估手段"]},
        {"id": "content", "name": "主要研究内容、关键技术与创新点", "word_limit": 800, "required": ["主要研究内容", "关键技术", "创新点"]},
        {"id": "approach", "name": "技术方案、技术路线与计划进度", "required": ["技术方案", "技术路线", "分阶段计划进度(含每阶段量化指标)"]},
        {"id": "outcomes", "name": "预期成果与推广措施", "required": ["预期成果形式", "成果推广方案"]},
    ],
    "rules": ["考核指标须量化可验证", "外文名词首现给全称与缩写", "紧扣requirements/constraints，不得跑题"],
}


def _lst(items) -> str:
    if not items:
        return "（无）"
    if isinstance(items, str):
        return items
    return "\n".join(f"- {x}" for x in items)


def _fmt_deliverables(dels: List[dict]) -> str:
    if not dels:
        return "（待定）"
    lines = []
    for i, d in enumerate(dels, 1):
        name = d.get("name", "")
        req = d.get("requirement", "")
        md = d.get("metrics_direction", "")
        lines.append(f"{i}. {name}（对应要求：{req}；量化方向：{md}）")
    return "\n".join(lines)


def _fmt_facts(facts) -> str:
    """The blueprint's shared numeric ledger, as it appears in every chapter's
    rules block. Sections are written in independent calls and cannot see each
    other's prose, so anything quantitative that recurs has to be pinned here."""
    if not facts:
        return "（无统一台账）"
    lines = []
    for f in facts:
        if isinstance(f, dict):
            name, value = str(f.get("name", "")).strip(), str(f.get("value", "")).strip()
            if name and value:
                lines.append(f"   - {name}：{value}")
        elif str(f).strip():
            lines.append(f"   - {str(f).strip()}")
    return "\n".join(lines) if lines else "（无统一台账）"


def run_step0(llm, base_vars: Dict[str, Any], verbose=True) -> dict:
    """Generate the writing blueprint. Degrades to a minimal blueprint on failure."""
    raw = generate_with_retry(
        llm,
        WP.STEP0_SYSTEM,
        WP.fill(WP.STEP0_USER, base_vars),
    )
    data = parse_json(raw) if raw else {}
    if not isinstance(data, dict) or not data.get("thesis"):
        # fallback: 1:1 deliverables from requirements, empty extras
        reqs = base_vars.get("_requirements_list", [])
        data = {
            "thesis": base_vars.get("title", ""),
            "deliverables": [{"name": r[:24], "requirement": r, "metrics_direction": ""} for r in reqs],
            "key_methods": [],
            "novelty_angles": [],
            "facts": [],
        }
        if verbose:
            print("    [step0] blueprint fallback (parse failed)")
    data.setdefault("facts", [])
    if verbose:
        print(f"    [step0] thesis: {data.get('thesis','')[:50]}")
        print(f"    [step0] {len(data.get('deliverables',[]))} deliverables, "
              f"{len(data.get('key_methods',[]))} methods, {len(data.get('novelty_angles',[]))} novelty, "
              f"{len(data.get('facts',[]))} facts")
        for f in data.get("facts", []):
            if isinstance(f, dict):
                print(f"      · {f.get('name','')}：{f.get('value','')}")
    return data


def plan_structure(llm, task, verbose=True) -> dict:
    """When a task has no mandated `structure`, let the LLM decide the core-content
    section layout (instead of silently using a hardcoded default)."""
    v = {
        "program": task.get("program", "") or task.get("sponsor", ""),
        "title": task.get("title", ""),
        "background": task.get("background", ""),
        "requirements": _lst(task.get("requirements", [])),
        "constraints": _lst(task.get("constraints", [])),
        "output_language": WP.language_name(task.get("language")),
    }
    raw = generate_with_retry(llm, WP.STRUCTURE_PLANNER_SYSTEM, WP.fill(WP.STRUCTURE_PLANNER_USER, v))
    data = parse_json(raw) if raw else {}
    secs = data.get("core_sections") if isinstance(data, dict) else None
    if not secs:
        if verbose:
            print("    [structure] planner failed -> DEFAULT_STRUCTURE")
        return DEFAULT_STRUCTURE
    for i, s in enumerate(secs):
        s.setdefault("id", s.get("name", f"sec_{i+1}"))
        s.setdefault("required", [])
    if verbose:
        print(f"    [structure] planned {len(secs)} sections: {', '.join(s.get('name','') for s in secs)}")
    return {
        "template": data.get("template") or "科研项目申请书",
        "core_sections": secs,
        "rules": data.get("rules", []),
    }


def summarize(name: str, text: str, limit=220) -> str:
    body = text.strip().replace("\n", " ")
    return f"【{name}】{body[:limit]}{'…' if len(body) > limit else ''}"


def write_one_section(llm, sec, base_vars, blueprint, prev_summary):
    """One-pass draft for a section (一次成稿, no in-pipeline review)."""
    name = sec.get("name", "")
    required = sec.get("required", [])
    wl = sec.get("word_limit")
    required_str = _lst(required)

    v = dict(base_vars)
    v.update({
        "section_name": name,
        "required_elements": required_str,
        "word_limit": (f"{wl}字" if wl else "不限"),
        "prev_summary": prev_summary or "（暂无，已写章节为空）",
        "thesis": blueprint.get("thesis", ""),
        "deliverables": _fmt_deliverables(blueprint.get("deliverables", [])),
        "key_methods": _lst(blueprint.get("key_methods", [])),
        "novelty_angles": _lst(blueprint.get("novelty_angles", [])),
        "facts": _fmt_facts(blueprint.get("facts", [])),
    })

    # build writer system: 通则 + writer role + triggered modifiers, one substitution pass
    raw_system = WP.GENERAL_RULES + "\n\n" + WP.WRITER_SYSTEM + WP.modifiers_for(required_str, name)
    system = WP.fill(raw_system, v)
    return generate_with_retry(llm, system, WP.fill(WP.WRITER_USER, v)) or f"## {name}\n\n[待补充]"


_LEADING_HEADING = re.compile(r"^\s*#{1,2}\s+(.+?)\s*$")


def strip_leading_heading(draft: str, name: str) -> str:
    """Drop the section title the writer repeats at the top of its own draft.

    The assembler emits the canonical `## {name}`, so a draft opening with its own
    copy renders the heading twice. Matching is exact, mirroring `_normalize_md`
    in md_to_docx.py: a leading heading that is NOT the section title is a real
    subheading the writer chose (e.g. 预期成果形式 under 项目成果及推广措施), and
    md_to_docx demotes those to `###` — so they must survive assembly intact.
    """
    lines = draft.split("\n")
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i >= len(lines):
        return draft
    m = _LEADING_HEADING.match(lines[i])
    if m and m.group(1).strip() == name.strip():
        return "\n".join(lines[i + 1:]).lstrip("\n")
    return draft


def _img_md(fig_id, i, caption, lang="zh"):
    return (f"\n\n![{fig_id}](figures/{fig_id}.png)\n\n"
            f"*{WP.figure_label(lang, i + 1, caption)}*\n\n")


def _split_marker(inner):
    """[figure: <caption> || <detailed prompt>] -> (caption, prompt).
    Falls back to a derived caption if a single-part marker was emitted."""
    if "||" in inner:
        cap, desc = inner.split("||", 1)
        cap, desc = cap.strip(), desc.strip()
    else:
        desc = inner.strip()
        cap = (desc[:24] + "…") if len(desc) > 24 else desc
    return (cap or desc[:24]), desc


def extract_figure_slots(proposal_text):
    """Each [figure:] marker -> one figure slot: the caption the writer wrote, the
    slot description handed to the planner, and the exact marker to replace."""
    out = []
    for i, m in enumerate(re.finditer(r"\[figure:\s*([^\]]+)\]", proposal_text)):
        cap, desc = _split_marker(m.group(1))
        out.append({"id": f"fig_{i+1:02d}", "caption": cap, "description": desc,
                    "marker": m.group(0)})
    return out


def figure_context(proposal_text, task):
    """Context for the planner: the task's own framing plus the opening of the
    draft. Without it the planner invents research objects to fill the canvas."""
    head = "\n".join([
        task.get("title", ""),
        task.get("background", ""),
        _lst(task.get("requirements", [])),
    ]).strip()
    return f"{head}\n\n{proposal_text[:5000]}"


def plan_slots(llm, slots, title, context, max_figures, lang="zh"):
    """Stage 1 for every slot, in order. Slots past --max-figures are not planned
    at all — planning costs an LLM call each."""
    for i, slot in enumerate(slots):
        if i >= max_figures:
            slot["plan"] = {"action": "skip", "reason": f"超出 --max-figures={max_figures}"}
            continue
        print(f"    [plan] {slot['id']}: {slot['caption'][:40]}...")
        plan = plan_figure(llm, slot["id"], title, slot["description"], context=context,
                           language=lang)
        if plan.get("action") != "draw":
            print(f"    [plan] {slot['id']} SKIP: {plan.get('reason','')[:80]}")
        else:
            print(f"    [plan] {slot['id']} -> {plan['composition']} | {plan['title']}")
        slot["plan"] = plan
    return slots


def _patch(text, slots, out_dir, img_cfg, draw=True, lang="zh"):
    """Replace each marker with an image reference (rendering it first when
    `draw`) or with a text placeholder. Returns (text, manifest)."""
    manifest = []
    for i, slot in enumerate(slots):
        plan = slot.get("plan") or {}
        # The planner's 图题 beats the writer's caption: it was written after the
        # figure's content was decided. Both follow the document's language.
        cap = plan.get("title") or slot["caption"]
        path = None
        if draw and plan.get("action") == "draw" and img_cfg.get("api_key"):
            print(f"    [img] {slot['id']}: {cap[:40]}...")
            path = render_figure(plan["image_prompt_en"], out_dir / "figures" / f"{slot['id']}.png",
                                 cfg=img_cfg)
            if path:
                write_plan_record(path, plan)
            else:
                print(f"    [img] {slot['id']} FAILED -> 文字占位")
        if path:
            text = text.replace(slot["marker"], _img_md(slot["id"], i, cap, lang), 1)
        else:
            text = text.replace(slot["marker"],
                                f"\n\n**[{WP.figure_label(lang, i + 1, cap)}]**\n\n", 1)
        manifest.append({
            "id": slot["id"], "caption": cap, "note": plan.get("caption", ""),
            "composition": plan.get("composition", ""),
            "description": slot["description"],
            "skip_reason": plan.get("reason", ""),
            "image": f"figures/{slot['id']}.png" if path else None,
        })
    return text, manifest


def process_figures(proposal_text, out_dir, img_cfg, max_figures, mode, llm, task):
    """mode: 'go' plan+draw+patch | 'dry' plan only, keep markers | 'off' text placeholder."""
    lang = WP.normalize_language(task.get("language"))
    slots = extract_figure_slots(proposal_text)
    if mode == "off" or not slots:
        return _patch(proposal_text, slots, out_dir, img_cfg, draw=False, lang=lang) + (slots,)

    slots = plan_slots(llm, slots, task.get("title", ""),
                       figure_context(proposal_text, task), max_figures, lang)
    if mode == "dry":
        # Keep the markers in place so --figures-from can patch them later.
        manifest = [{"id": s["id"], "caption": (s.get("plan") or {}).get("title") or s["caption"],
                     "description": s["description"], "image": None} for s in slots]
        return proposal_text, manifest, slots

    text, manifest = _patch(proposal_text, slots, out_dir, img_cfg, draw=True, lang=lang)
    return text, manifest, slots


def generate_from_dir(out_dir: Path, img_cfg, max_figures):
    """Render images for a prior --figures dry run from its saved plans, and patch
    proposal_final.md in place. Does NOT regenerate any text or re-plan."""
    slots = json.loads((out_dir / "figure_plans.json").read_text(encoding="utf-8"))
    md = (out_dir / "proposal_final.md").read_text(encoding="utf-8")
    task_path = out_dir / "task.json"
    lang = WP.normalize_language(
        json.loads(task_path.read_text(encoding="utf-8")).get("language")
        if task_path.exists() else "zh")
    md, manifest = _patch(md, slots[:max_figures] + [
        dict(s, plan={"action": "skip", "reason": f"超出 --max-figures={max_figures}"})
        for s in slots[max_figures:]
    ], out_dir, img_cfg, draw=True, lang=lang)
    (out_dir / "proposal_final.md").write_text(md, encoding="utf-8")
    json.dump(manifest, (out_dir / "figure_manifest.json").open("w", encoding="utf-8"), indent=2, ensure_ascii=False)
    imgs = sum(1 for m in manifest if m["image"])
    print(f"DONE figures {imgs}/{len(manifest)} -> {out_dir}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", default="", help="path/id of task json (not needed with --figures-from)")
    ap.add_argument("--out", default="")
    ap.add_argument("--sections", type=int, default=0, help="limit to first N sections (0=all)")
    ap.add_argument("--max-figures", type=int, default=5)
    ap.add_argument("--no-images", action="store_true", help="alias for --figures off")
    ap.add_argument("--figures", choices=["go", "dry", "off"], default="go",
                    help="go=generate+patch | dry=write prompts & keep markers, no gen | off=text placeholder")
    ap.add_argument("--figures-from", default="", help="generate images for an existing --figures dry run dir; skips all text gen")
    ap.add_argument("--judge", choices=["none", "rubric"], default="none",
                    help="rubric = 7-dimension panel (see scripts/evaluate.py)")
    ap.add_argument("--judge-evidence", action="store_true",
                    help="retrieve external literature for the science judge")
    args = ap.parse_args()

    if args.figures_from:
        print(f"== 仅生图（从已确认的 prompts）: {args.figures_from}")
        generate_from_dir(Path(args.figures_from), image_config_from_env(), args.max_figures)
        return

    if not args.task:
        sys.exit("ERROR: --task is required (unless using --figures-from)")

    task_path = Path(args.task)
    if not task_path.exists():
        task_path = Path("cases/tasks") / (args.task if args.task.endswith(".json") else f"{args.task}.json")
    task = json.loads(task_path.read_text(encoding="utf-8"))

    llm = backend_from_env()
    if not llm.api_key:
        sys.exit("ERROR: AI4PROPOSAL_API_KEY not set")

    structure = task.get("structure")
    if not (structure and structure.get("core_sections")):
        print("  [structure] 任务未提供 structure，由 LLM 规划核心章节 ...")
        structure = plan_structure(llm, task)
    core_sections = structure.get("core_sections") or DEFAULT_STRUCTURE["core_sections"]
    if args.sections:
        core_sections = core_sections[: args.sections]

    case_id = task.get("task_id", "task")
    out_dir = Path(args.out) if args.out else Path("outputs") / case_id
    out_dir.mkdir(parents=True, exist_ok=True)

    # base variables shared by all prompts
    lang = WP.normalize_language(task.get("language"))
    base_vars = {
        "lang_rules": WP.lang_rules_for(lang),
        "output_language": WP.language_name(lang),
        "title": task.get("title", ""),
        "background": task.get("background", ""),
        "challenges": _lst(task.get("challenges", [])),
        "requirements": _lst(task.get("requirements", [])),
        "constraints": _lst(task.get("constraints", [])),
        "template": structure.get("template", "科研项目申请书"),
        "rules": _lst(structure.get("rules", [])),
        "_requirements_list": task.get("requirements", []),
    }

    print(f"== {case_id}  {task.get('title','')}")
    print(f"   template: {base_vars['template']} | model: {llm.model} | "
          f"sections: {len(core_sections)} | 正文语言: {base_vars['output_language']}")

    t0 = time.time()
    print("  [1/4] Step0 蓝图 ...")
    blueprint = run_step0(llm, base_vars)

    print("  [2/4] 逐章撰写 ...")
    sections: Dict[str, str] = {}
    prev_summary = ""
    for sec in core_sections:
        draft = write_one_section(llm, sec, base_vars, blueprint, prev_summary)
        sid = sec.get("id", sec.get("name"))
        sections[sid] = draft
        prev_summary = (prev_summary + "\n" + summarize(sec.get("name", ""), draft)).strip()
        print(f"    [OK] {sec.get('name','')} ({len(draft)}字)")
        (out_dir / f"{sid}.md").write_text(draft, encoding="utf-8")

    # assemble
    parts = [f"# {task.get('title','')}\n"]
    for sec in core_sections:
        sid = sec.get("id", sec.get("name"))
        name = sec.get("name", "")
        parts.append(f"## {name}\n\n{strip_leading_heading(sections.get(sid, ''), name)}")
    proposal_text = "\n\n".join(parts)

    fig_mode = "off" if args.no_images else args.figures
    print(f"  [3/4] 出图 (mode={fig_mode}) ...")
    img_cfg = {"api_key": "", "base_url": "", "model": "", "size": ""} if fig_mode == "off" else image_config_from_env()
    proposal_text, manifest, fig_slots = process_figures(
        proposal_text, out_dir, img_cfg, args.max_figures, fig_mode, llm, task
    )

    (out_dir / "proposal_final.md").write_text(proposal_text, encoding="utf-8")
    json.dump(manifest, (out_dir / "figure_manifest.json").open("w", encoding="utf-8"), indent=2, ensure_ascii=False)
    json.dump(fig_slots, (out_dir / "figure_plans.json").open("w", encoding="utf-8"), indent=2, ensure_ascii=False)

    if fig_mode == "dry":
        print(f"\n  [dry] 共 {len(fig_slots)} 个图位（已规划，未生图，markers 保留）：")
        for s in fig_slots:
            p = s.get("plan") or {}
            if p.get("action") != "draw":
                print(f"\n  ── {s['id']} ── SKIP: {p.get('reason','')}")
                continue
            print(f"\n  ── {s['id']} ── {p['composition']}\n  图题: {p['title']}\n  论点: {p['main_message']}\n  prompt: {p['image_prompt_en']}")
        print(f"\n  确认后执行：python scripts/run_pipeline.py --figures-from {out_dir}")
    json.dump(blueprint, (out_dir / "blueprint.json").open("w", encoding="utf-8"), indent=2, ensure_ascii=False)
    # Write the structure actually used back onto the task. A planned structure
    # used to be discarded here, so evaluate.py fell through to a hardcoded
    # Chinese section list and reported an English proposal as missing
    # 项目成果及推广措施 — a section it never had. That tripped the missing-section
    # gate and capped an 81/100 run at revise_resubmit.
    task["structure"] = structure
    json.dump(task, (out_dir / "task.json").open("w", encoding="utf-8"), indent=2, ensure_ascii=False)

    result = {
        "case_id": case_id, "title": task.get("title", ""),
        "figures": manifest,
        "char_count": len(proposal_text), "model": llm.model,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    print("  [4/4] 评分 ...")
    if args.judge == "rubric":
        try:
            from ai4proposal.evaluation import RubricPanel
            pack = None
            if args.judge_evidence:
                from ai4proposal.evidence import gather_evidence
                from ai4proposal.llm import cheap_backend
                pack = gather_evidence(llm, proposal_text, verbose=True,
                                       rerank_llm=cheap_backend(llm))
            jr = RubricPanel(llm, verbose=True).evaluate(proposal_text, task, evidence=pack)
            result["judge"] = jr.to_dict()
            print(f"      overall={jr.overall_100}/100 verdict={jr.verdict}")
        except Exception as e:
            result["judge"] = {"error": str(e)}
            print(f"      judge error: {e}")
    else:
        print("      skipped (评分请用 scripts/evaluate.py 或 --judge rubric)")

    json.dump(result, (out_dir / "result.json").open("w", encoding="utf-8"), indent=2, ensure_ascii=False)
    imgs = sum(1 for m in manifest if m["image"])
    print(f"\nDONE in {time.time()-t0:.0f}s | {len(proposal_text)}字 | 图{imgs}/{len(manifest)} | -> {out_dir}")


if __name__ == "__main__":
    main()
