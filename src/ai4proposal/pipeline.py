"""V4 Pipeline: Step-by-step section generation + iterative review loop.

Key fix: generate each section INDIVIDUALLY to avoid API timeout on long outputs.
Each section: Writer → Reviewer → (Reviser if score<7) → next section.
"""
from __future__ import annotations

import json, re, time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .llm import LLMBackend
from .ai_judge import AIJudge

def _evaluate_proposal(text: str, call: dict, judge: Optional["AIJudge"]) -> dict:
    """Run AI Judge on proposal + basic stats."""
    wc = len(text.split())
    cc = len(text)
    result = {"basic": {"word_count": wc, "char_count": cc, "standard_sections_found": 0, "sections_total": 7}}
    if judge:
        try:
            jr = judge.evaluate(text, call, {})
            result["ai_judge"] = {"scores": jr.scores, "overall_score": jr.overall_score,
                                   "strengths": jr.strengths, "weaknesses": jr.weaknesses,
                                   "verdict": jr.verdict, "summary": jr.summary}
            result["quality_score"] = round(jr.overall_score * 10, 1)
            result["verdict"] = jr.verdict
        except Exception as e:
            result["ai_judge"] = {"error": str(e)}
            result["quality_score"] = 50.0
            result["verdict"] = "error"
    return result


# ═══ Section definitions for Chinese NSFC proposal ═══
NSFC_SECTIONS = [
    ("sec_01_summary", "项目摘要", "Executive Summary — 500字以内，涵盖问题、目标、方法、预期成果、意义"),
    ("sec_02_background", "研究背景与立项依据", "2-3段。当前SOTA + 关键gap + 本项目要解决的问题。必须引用[1][2]等文献"),
    ("sec_03_objectives", "研究目标与关键科学问题", "3-4个具体目标 + 2-3个关键科学问题"),
    ("sec_04_content", "研究内容", "3-4项研究内容，每项300-500字。详细技术方案"),
    ("sec_05_approach", "技术方案与技术路线", "整体技术路线 + 关键技术详解 + [figure: technical_route]"),
    ("sec_06_innovation", "创新点", "3个具体创新点，每个100-200字"),
    ("sec_07_feasibility", "可行性分析", "理论/数据/算力可行性 + 风险与对策"),
    ("sec_08_foundation", "研究基础与条件", "PI简历 + 团队 + 前期成果 + 发表论文列表 + 设备条件"),
    ("sec_09_timeline", "计划进度与预期成果", "3年时间线 + 每阶段里程碑 + 定量考核指标 + [figure: timeline_gantt]"),
    ("sec_10_budget", "经费预算", "详细预算表（设备费/材料费/测试费/差旅费/劳务费/专家咨询费） + 测算依据"),
]

# ═══ Prompts ═══
SECTION_WRITER_SYSTEM = """You are an expert grant proposal writer specializing in NSFC (国家自然科学基金) proposals. Write professional, detailed, technically substantive content in Chinese. Each section must be complete and standalone."""

SECTION_WRITER_USER = """Write the "{section_name}" section for the following research proposal.

## Research Topic
Title: {title}
Sponsor: {sponsor}
Budget: {budget}

## Research Background
{abstract}

## Team
{team_info}

## Section Requirements
{section_guide}

## Previously Written Sections (for context)
{previous_sections}

## Instructions
- Write DETAILED, SPECIFIC content (300-600 words for this section)
- Use professional Chinese academic language
- Only insert [figure: detailed_description_in_chinese] at KEY sections: 技术方案(1个) and 计划进度(1个). At most 5 figures total across the ENTIRE proposal.
- Each [figure: ...] description must be DETAILED (50+ chars in Chinese) — specific about what to draw, style, colors, layout
- Reference prior sections where relevant
- Reference prior sections where relevant
- Do NOT repeat content from previous sections

Write the "{section_name}" section now:"""

SECTION_REVIEW_SYSTEM = """You are a rigorous NSFC proposal reviewer. Score this section from 1-10 and provide specific feedback."""

SECTION_REVIEW_USER = """Review the "{section_name}" section of this proposal.

## Proposal Context
Title: {title}

## Section Content
{section_content}

## Criteria
- Content completeness and depth (300+ words expected)
- Technical specificity (not generic)
- Professional academic language
- Alignment with the overall proposal theme

Respond with ONLY this JSON:
{{"score": <1-10>, "strengths": ["..."], "weaknesses": ["..."], "suggestions": ["..."], "pass": <true if score >= 7>}}"""

SECTION_REVISE_SYSTEM = """You are an expert NSFC proposal writer. Revise this section based on reviewer feedback."""

SECTION_REVISE_USER = """Revise the "{section_name}" section.

## Original Section
{original}

## Reviewer Feedback
Score: {score}/10
Strengths: {strengths}
Weaknesses: {weaknesses}
Suggestions: {suggestions}

## Instructions
- Address ALL weaknesses and suggestions
- Keep content that was praised as strengths
- Maintain professional Chinese academic language
- Expand content to be more detailed and specific
- Target 400-700 words

Revised "{section_name}" section:"""


@dataclass
class PipelineConfig:
    use_llm: bool = True
    use_review: bool = True
    max_revisions: int = 2
    model: str = ""
    api_key: str = ""
    base_url: str = ""

    @classmethod
    def from_env(cls) -> "PipelineConfig":
        import os
        return cls(
            use_llm=os.environ.get("AI4PROPOSAL_USE_LLM", "1").lower() in ("1", "true", "yes"),
            use_review=os.environ.get("AI4PROPOSAL_USE_REVIEW", "1").lower() in ("1", "true", "yes"),
            max_revisions=int(os.environ.get("AI4PROPOSAL_MAX_REVISIONS", "2")),
            model=os.environ.get("AI4PROPOSAL_MODEL", "gpt-4.1"),
            api_key=os.environ.get("AI4PROPOSAL_API_KEY", ""),
            base_url=os.environ.get("AI4PROPOSAL_BASE_URL", "https://api.openai.com/v1"),
        )


def _make_llm(config: PipelineConfig) -> Optional[LLMBackend]:
    if not config.use_llm or not config.api_key:
        return None
    return LLMBackend(model=config.model, api_key=config.api_key,
                       base_url=config.base_url, timeout_seconds=180.0)


def _generate(llm, system: str, prompt: str, fallback: str = "") -> str:
    if llm is None:
        return fallback
    for attempt in range(4):
        try:
            return llm.generate_text(system_prompt=system, user_prompt=prompt)
        except Exception as e:
            msg = str(e)[:100]
            print(f"    [retry {attempt+1}: {msg}]")
            if attempt < 3:
                time.sleep((attempt + 1) * 10)
    return fallback


def _parse_json(text: str) -> Dict[str, Any]:
    try:
        s = text.find("{"); e = text.rfind("}") + 1
        if s >= 0 and e > s:
            return json.loads(text[s:e])
    except Exception:
        pass
    return {}


def run_case(case_dir: Path, output_dir: Path, config: Optional[PipelineConfig] = None) -> Dict[str, Any]:
    """V4: Step-by-step section generation with review loop."""
    if config is None:
        config = PipelineConfig()

    call = json.loads((case_dir / "call.json").read_text())
    team = json.loads((case_dir / "team.json").read_text())
    llm = _make_llm(config)

    if output_dir.exists():
        import shutil; shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)
    json.dump(call, (output_dir / "call.json").open("w"), indent=2, ensure_ascii=False)
    json.dump(team, (output_dir / "team.json").open("w"), indent=2, ensure_ascii=False)

    lang = call.get("language", "zh")
    sections_def = NSFC_SECTIONS

    # Format team info
    team_parts = [f"PI: {team.get('pi_name', 'TBD')} @ {team.get('institution', 'TBD')}"]
    for key, label in [("phd_students", "博士生"), ("master_students", "硕士生")]:
        members = team.get(key, [])
        if members:
            names = ", ".join(m["name"] for m in members)
            team_parts.append(f"{label}: {names}")
    team_info = "; ".join(team_parts)

    budget_str = f"{call.get('budget', {}).get('amount', 'N/A')} {call.get('budget', {}).get('currency', 'CNY')}"

    # ═══ Step 1: Generate each section one at a time ═══
    print(f"  [1/4] Generating {len(sections_def)} sections step-by-step...")
    sections = {}  # section_id → text
    section_scores = {}  # section_id → score

    for sec_id, sec_name, sec_guide in sections_def:
        # Build context from previous sections
        prev_text = "\n\n".join(
            f"### {name}\n{text[:500]}..."
            for (sid, name, _), (s_text) in zip(sections_def, sections.values())
            if sid == sec_id  # stop at current
        )
        prev_summary = "\n".join(
            f"- {name}: written ({len(sections.get(sid2, ''))} chars)"
            for sid2, name, _ in sections_def
            if sid2 in sections
        )

        # Generate section
        prompt = SECTION_WRITER_USER.format(
            section_name=sec_name, title=call["title"],
            sponsor=call.get("sponsor", ""), budget=budget_str,
            abstract=call.get("abstract", ""),
            team_info=team_info,
            section_guide=sec_guide,
            previous_sections=prev_summary or "(This is the first section)",
        )
        text = _generate(llm, SECTION_WRITER_SYSTEM, prompt)
        if not text:
            text = f"## {sec_name}\n\n[Content pending]"

        sections[sec_id] = text
        print(f"    ✓ {sec_name} ({len(text)} chars)")

        # Review + Revise loop
        if config.use_review and llm:
            for rev_round in range(config.max_revisions):
                review_prompt = SECTION_REVIEW_USER.format(
                    section_name=sec_name, title=call["title"],
                    section_content=text,
                )
                review_raw = _generate(llm, SECTION_REVIEW_SYSTEM, review_prompt)
                review = _parse_json(review_raw)
                score = review.get("score", 5)

                if score >= 7:
                    section_scores[sec_id] = score
                    break

                # Revise
                print(f"      Revising (score={score})...")
                revise_prompt = SECTION_REVISE_USER.format(
                    section_name=sec_name, original=text, score=score,
                    strengths="; ".join(review.get("strengths", [])),
                    weaknesses="; ".join(review.get("weaknesses", [])),
                    suggestions="; ".join(review.get("suggestions", [])),
                )
                text = _generate(llm, SECTION_REVISE_SYSTEM, revise_prompt, fallback=text)
                sections[sec_id] = text
                section_scores[sec_id] = score

        # Save intermediate
        (output_dir / f"{sec_id}.md").write_text(text)

    # ═══ Step 2: Assemble full proposal ═══
    print(f"  [2/4] Assembling full proposal...")
    full_proposal_parts = [f"# {call['title']}\n"]
    for sec_id, sec_name, _ in sections_def:
        if sec_id in sections:
            full_proposal_parts.append(f"## {sec_name}\n\n{sections[sec_id]}")
    proposal_text = "\n\n".join(full_proposal_parts)
    (output_dir / "proposal_assembled.md").write_text(proposal_text)

    # ═══ Step 3: Figure processing ═══
    print(f"  [3/4] Processing [figure] markers...")
    figures_found = re.findall(r'\[figure:\s*([^\]]+)\]', proposal_text)
    for i, fig_desc in enumerate(figures_found):
        anchor = f"\n\n**[Figure {i+1}: {fig_desc.strip()}]**\n\n"
        proposal_text = proposal_text.replace(f"[figure: {fig_desc}]", anchor, 1)
    json.dump([{"id": f"fig_{i+1:02d}", "description": d.strip()}
               for i, d in enumerate(figures_found)],
              (output_dir / "figure_manifest.json").open("w"), indent=2, ensure_ascii=False)
    print(f"      {len(figures_found)} figures placed")

    # ═══ Step 4: AI Judge ═══
    print(f"  [4/4] AI Judge holistic scoring...")
    judge = AIJudge(llm) if llm else None
    score = _evaluate_proposal(proposal_text, call, judge)
    score["section_scores"] = section_scores
    score["metadata"] = {
        "case_id": call["case_id"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": config.model,
        "pipeline": "v4_stepwise",
        "sections_generated": len(sections),
        "figures_found": len(figures_found),
    }
    json.dump(score, (output_dir / "score.json").open("w"), indent=2, ensure_ascii=False)
    (output_dir / "proposal_final.md").write_text(proposal_text)

    qs = score.get("quality_score", "N/A")
    wc = score["basic"]["word_count"]
    avg_sec = sum(section_scores.values()) / max(1, len(section_scores))
    print(f"  ✓ Quality: {qs} | Words: {wc} | Avg section score: {avg_sec:.1f} | Figures: {len(figures_found)}")
    return score
