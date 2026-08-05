"""Panel-of-judges evaluation for the CORE content of a research proposal.

The writing pipeline now produces only the *core research content* — objectives /
KPIs / R&D content & innovation / technical approach & schedule / outcomes — driven by
`task.structure.core_sections`. It does NOT produce team, research基础 or budget. This
panel evaluates exactly that core content.

Four specialist reviewers each read the full core proposal through their own lens and
score fine-grained sub-dimensions (reason-before-score, JSON only) plus emit structured
coverage checklists. A chair consolidates qualitative strengths / weaknesses / summary.
The CODE computes the weighted overall_score and the verdict — never the LLM.

De-specialised: the fixed prompt text names no funder / discipline; program / template /
requirements / constraints / expected sections are injected via ${...} from the task.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from string import Template
from typing import Any, Dict, List, Optional

from .evidence import NEUTRAL_EVIDENCE

# ── Aggregation weights (code-computed overall). Tunable. Sum = 1.00 ──
WEIGHTS: Dict[str, float] = {
    "kpi_rigor":      0.18,   # 考核指标硬度：量化、可第三方验证、覆盖全部硬性要求
    "methodology":    0.16,   # 技术方案与路线：具体可落地、闭环
    "innovation":     0.15,   # 创新性："现有→本课题"的真差异
    "feasibility":    0.13,   # 可行性：指标可达、进度合理、有风险预案
    "specificity":    0.12,   # 具体性：点名真实方法/基准/数值，无空话
    "research_value": 0.10,   # 研究价值：立项依据成立、关键问题重要且清晰
    "compliance":     0.10,   # 合规：满足硬性约束 + 不跑题 + 结构/字数
    "clarity":        0.06,   # 表达：主线贯穿、逻辑、可读
}

MAX_PROPOSAL_CHARS = 24000

# Fallback expected sections when a task declares no structure.
DEFAULT_CORE_SECTIONS = [
    "课题目标", "考核指标", "主要研发内容、关键技术及创新点",
    "技术方案、技术路线及计划进度", "项目成果及推广措施",
]

# roles whose prompt consumes external ${evidence}
EVIDENCE_ROLES = {"science_innovation", "method_feasibility", "kpi_requirements"}

# ═══════════════════════════════ Specialist prompts ═══════════════════════════════

SCIENCE_SYSTEM = """你是本资助计划的资深评审专家，专精"研究价值与创新性"判断。你通读本子核心内容，只对研究价值与创新性负责。你严格区分"真实的方法/理论创新"与"华丽措辞包装的增量工作"，并主动识别过度声称（如"首次""可证明""填补空白""国际领先"却无支撑）。评分先写依据后给分。只输出 JSON，不要 markdown。"""

SCIENCE_USER = Template("""评审以下申请书核心内容的【研究价值与创新性】。

## 课题信息
标题：${title}
资助计划：${program}
立项依据/背景：${abstract}

## 本方向的开放挑战
${challenges}

## 待评审正文（核心内容）
${proposal_text}

## 外部文献证据（用于核查创新性与过度声称，判断权在你，勿被检索结果直接左右）
${evidence}

## 评分维度与分档锚点（1-10）
research_value（研究价值）：
  9-10 立项依据扎实、关键问题重要且清晰、切中真实需求
  7-8  问题明确、依据基本成立但深度一般
  5-6  问题偏泛或依据笼统
  3-4  需求牵强、论证松散
  1-2  缺乏有价值的问题
innovation（创新性）：
  9-10 相对现有工作有实质方法/理论创新，"现有→本课题"差异清晰真实
  7-8  有一定新意，多为已知技术的新组合
  5-6  增量改进，创新表述空泛
  3-4  基本无创新或仅换名词
  1-2  照搬已有工作

## 要求
- 对照背景与文献判断"新在哪"，不被措辞带节奏。
- 列出识别到的过度声称(over_claims)，无则空数组。
- 每维先 reason 后 score。

只输出：
{
  "role": "science_innovation",
  "dimensions": {
    "research_value": {"reason": "...", "score": 0},
    "innovation": {"reason": "...", "score": 0}
  },
  "over_claims": ["..."],
  "notes": "一句总体印象"
}""")

METHOD_SYSTEM = """你是本资助计划的资深评审专家，专精"技术方案与可行性"。你通读核心内容，重点判断方案是否具体可落地、技术路线是否闭环、是否真正针对本方向挑战、指标是否可达、有无风险预案。你警惕"只有概念、缺关键技术细节/步骤"的方案。评分先写依据后给分。只输出 JSON。"""

METHOD_USER = Template("""评审以下申请书核心内容的【技术方案与可行性】。

## 课题信息
标题：${title}
立项依据/背景：${abstract}
硬性约束（周期/资源/强制技术/方向）：${constraints}

## 本方向需解决的开放挑战（逐条勾对）
${challenges}

## 待评审正文（核心内容）
${proposal_text}

## 外部文献证据（用于核对方法真实能力与指标合理性）
${evidence}

## 评分维度与分档锚点（1-10）
methodology（技术方案与路线）：
  9-10 有清晰的方法/流程与技术路线，关键技术可操作、要素完整、前后闭环
  7-8  方案较具体但部分环节停留在思路
  5-6  多为概念性描述，缺关键细节或路线断裂
  3-4  空泛口号
feasibility（可行性）：
  9-10 指标可达、进度合理、与约束/资源匹配、有风险预案
  7-8  方案合理但风险或进度考虑不足
  5-6  目标偏理想、可行性论证薄弱
  3-4  明显不可行或自相矛盾

## 要求
- 对上面每一条 challenge 判断被解决程度：full / partial / none，并给一句 evidence 指向文中做法。
- 指出停留在"概念描述、缺关键技术细节"的薄弱环节(weak_points)。
- 每维先 reason 后 score。

只输出：
{
  "role": "method_feasibility",
  "dimensions": {
    "methodology": {"reason": "...", "score": 0},
    "feasibility": {"reason": "...", "score": 0}
  },
  "challenge_coverage": [
    {"challenge": "挑战简述", "level": "full", "evidence": "..."}
  ],
  "weak_points": ["..."],
  "notes": "一句总体印象"
}""")

KPI_SYSTEM = """你是本资助计划的资深评审专家，专精"考核指标与内容具体性"。你通读核心内容，最看重两点：考核指标是否量化、可由第三方独立验证、是否覆盖资助方全部硬性交付要求；正文是否点名真实方法/基准/数值、有无空话套语。这是最能区分优劣、最可核查的维度，从严打分。评分先写依据后给分。只输出 JSON。"""

KPI_USER = Template("""评审以下申请书核心内容的【考核指标硬度与内容具体性】。

## 课题信息
标题：${title}
资助计划：${program}

## 资助方硬性交付要求（逐条勾对：是否被明确承诺，且被某考核指标量化覆盖）
${requirements}

## 待评审正文（核心内容）
${proposal_text}

## 外部文献证据（用于核对指标合理性）
${evidence}

## 评分维度与分档锚点（1-10）
kpi_rigor（考核指标硬度）：
  9-10 指标全部量化、带数值与口径、可第三方验证、覆盖全部硬性要求、标注立项/完成值与考核方式
  7-8  多数指标量化，个别偏软或验证方式不清
  5-6  指标部分量化，存在"提升能力/优化性能"等不可度量表述，或未覆盖部分要求
  3-4  多为定性描述、难以验证
  1-2  无实质考核指标
specificity（内容具体性）：
  9-10 全程点名真实方法/工具/基准并给具体数值/参数，几乎无空话
  7-8  较具体，偶有笼统
  5-6  空泛描述较多，实名与数值偏少
  3-4  通篇概念化、套语堆砌
  1-2  几乎全是空话

## 要求
- 对每条硬性要求判断覆盖程度：covered / partial / none（covered 需既被明确承诺、又被某可量化考核指标覆盖），给一句 evidence。
- 列出命中的空话套语(empty_phrases)，如"大幅提升""具有重要意义""国际领先""深入研究"等；无则空数组。
- 每维先 reason 后 score。

只输出：
{
  "role": "kpi_requirements",
  "dimensions": {
    "kpi_rigor": {"reason": "...", "score": 0},
    "specificity": {"reason": "...", "score": 0}
  },
  "requirements_coverage": [
    {"requirement": "要求简述", "level": "covered", "evidence": "..."}
  ],
  "empty_phrases": ["..."],
  "notes": "一句总体印象"
}""")

COMPLIANCE_SYSTEM = """你是本资助计划的形式审查与写作评审专家。你对照应含章节清单核对完整性与实质内容，检查是否满足硬性约束、是否跑题、结构与字数是否合规，并评估表达与可读性。注意：本子仅含核心研究内容，不含团队/研究基础/经费预算，**不要**因缺少这些而扣分，也**不要**评估预算合理性。评分先写依据后给分。只输出 JSON。"""

COMPLIANCE_USER = Template("""评审以下申请书核心内容的【合规性与表达】。

## 本资助计划应含的核心章节（逐项核对是否齐全且有实质内容）
${sections_expected}

## 硬性约束（须逐条满足、不得跑题）
${constraints}

## 待评审正文（核心内容）
${proposal_text}

## 评分维度与分档锚点（1-10）
compliance（合规）：
  9-10 满足全部硬性约束、不跑题、应含章节齐全且各有实质、结构/字数合规
  7-8  基本合规，个别约束贴合不紧或某章偏薄
  5-6  违反 1 条约束、或缺 1 章、或多章空泛、或有跑题倾向
  3-4  多处违规/结构残缺/跑题
clarity（表达）：
  9-10 主线贯穿、结构清晰、逻辑连贯、术语准确、无冗余
  7-8  基本清晰但有冗余或重复
  5-6  组织松散、重复较多
  3-4  难以阅读

## 要求
- 输出 section_checklist：对每个应含章节给 present: true/false + 一句 substance 评价。
- 列出违反的硬性约束(constraint_violations)，无则空数组。
- 每维先 reason 后 score。

只输出：
{
  "role": "compliance_writing",
  "dimensions": {
    "compliance": {"reason": "...", "score": 0},
    "clarity": {"reason": "...", "score": 0}
  },
  "section_checklist": [
    {"section": "章节名", "present": true, "substance": "..."}
  ],
  "constraint_violations": ["..."],
  "notes": "一句总体印象"
}""")

# ═══════════════════════════════ Chair prompt (qualitative only) ═══════════════════════════════

CHAIR_SYSTEM = """你是本资助计划的会评专家组组长。四位专项评审已给出结构化意见，加权总分与资助结论已由系统按既定规则算出。你只做定性汇总：提炼优缺点、撰写总体评语，不修改任何分数或结论。只输出 JSON。"""

CHAIR_USER = Template("""标题：${title}

## 四位专项评审意见（JSON）
${reviews_json}

## 系统已计算的结论（不可修改，供撰写评语参考）
各维度分：${scores_json}
加权总分：${overall_score}
资助结论：${verdict}

## 要求
- strengths / weaknesses 各 3-5 条，从专项意见中提炼具体内容，不要泛泛。
- 若存在未覆盖的硬性要求、违反的约束、缺失章节、过度声称或空话，须在 weaknesses 点明。
- summary 一段话，需与上述资助结论口径一致。

只输出：
{
  "strengths": ["..."],
  "weaknesses": ["..."],
  "summary": "..."
}""")


# ── role registry: name → (system, user_template, dims produced) ──
SPECIALIST_ROLES = [
    ("science_innovation", SCIENCE_SYSTEM, SCIENCE_USER, ["research_value", "innovation"]),
    ("method_feasibility", METHOD_SYSTEM, METHOD_USER, ["methodology", "feasibility"]),
    ("kpi_requirements", KPI_SYSTEM, KPI_USER, ["kpi_rigor", "specificity"]),
    ("compliance_writing", COMPLIANCE_SYSTEM, COMPLIANCE_USER, ["compliance", "clarity"]),
]


@dataclass
class PanelResult:
    # Attribute names mirror ai_judge.JudgeResult so test_judge.print_result works.
    scores: Dict[str, float]
    overall_score: float
    strengths: List[str]
    weaknesses: List[str]
    verdict: str
    summary: str
    # Panel-specific detail:
    reviews: Dict[str, Any] = field(default_factory=dict)
    challenge_coverage: List[Dict[str, Any]] = field(default_factory=list)
    requirements_coverage: List[Dict[str, Any]] = field(default_factory=list)
    section_checklist: List[Dict[str, Any]] = field(default_factory=list)
    constraint_violations: List[str] = field(default_factory=list)
    over_claims: List[str] = field(default_factory=list)
    empty_phrases: List[str] = field(default_factory=list)
    raw_response: str = ""


def _parse_json(text: str) -> Dict[str, Any]:
    try:
        s = text.find("{")
        e = text.rfind("}") + 1
        if s >= 0 and e > s:
            return json.loads(text[s:e])
    except Exception:
        pass
    return {}


def _fmt_numbered(items: Any) -> str:
    if not items:
        return "（未提供）"
    if isinstance(items, str):
        return items
    return "\n".join(f"{i+1}. {c}" for i, c in enumerate(items))


def _sections_from_task(task: Dict[str, Any]) -> List[str]:
    st = task.get("structure") or {}
    secs = st.get("core_sections") or []
    names = [s.get("name", "") for s in secs if isinstance(s, dict) and s.get("name")]
    return names or DEFAULT_CORE_SECTIONS


def weighted_overall(scores: Dict[str, Optional[float]]) -> float:
    """Weighted mean over present dims; missing dims' weight is redistributed."""
    present = {k: v for k, v in scores.items() if v is not None and k in WEIGHTS}
    total_w = sum(WEIGHTS[k] for k in present)
    if total_w == 0:
        return 5.0
    return round(sum(present[k] * WEIGHTS[k] for k in present) / total_w, 2)


def decide_verdict(overall: float, has_uncovered_requirement: bool, has_missing_section: bool) -> str:
    if overall < 6.0:
        return "reject"
    if overall >= 7.5 and not has_uncovered_requirement and not has_missing_section:
        return "recommend_submit"
    return "revise_resubmit"


class PanelJudge:
    """Runs 4 specialist judges + a chair. Overall score & verdict computed in code."""

    def __init__(self, llm: Any, max_retries: int = 2, verbose: bool = False):
        self.llm = llm
        self.max_retries = max_retries
        self.verbose = verbose

    def _call(self, system: str, user: str) -> str:
        last = ""
        for attempt in range(self.max_retries + 1):
            try:
                return self.llm.generate_text(system_prompt=system, user_prompt=user)
            except Exception as e:  # LLMBackend has no internal retry
                last = f"{type(e).__name__}: {e}"
                if self.verbose:
                    print(f"      [retry {attempt+1}: {str(e)[:80]}]")
        raise RuntimeError(f"panel judge call failed after {self.max_retries+1} tries: {last}")

    def evaluate(
        self,
        proposal_text: str,
        task: Dict[str, Any],
        evidence: Optional[Any] = None,
    ) -> PanelResult:
        """`task` is the full task dict (as loaded by run_pipeline) — the judge reads
        title / background / program / challenges / requirements / constraints / structure
        directly from it, so no reduced `call` subset is needed."""
        if len(proposal_text) > MAX_PROPOSAL_CHARS:
            proposal_text = proposal_text[:MAX_PROPOSAL_CHARS] + "\n\n[... truncated ...]"

        sections = _sections_from_task(task)
        ctx = {
            "title": task.get("title", ""),
            "program": task.get("program", "") or task.get("sponsor", ""),
            "template": (task.get("structure") or {}).get("template", "") or "科研项目申请书",
            "abstract": (task.get("abstract") or task.get("background") or "")[:2000],
            "challenges": _fmt_numbered(task.get("challenges", [])),
            "requirements": _fmt_numbered(task.get("requirements", [])),
            "constraints": _fmt_numbered(task.get("constraints", [])),
            "sections_expected": "\n".join(f"- {s}" for s in sections),
            "proposal_text": proposal_text,
        }

        reviews: Dict[str, Any] = {}
        scores: Dict[str, Optional[float]] = {k: None for k in WEIGHTS}

        for name, system, template, dims in SPECIALIST_ROLES:
            if self.verbose:
                print(f"    [panel] {name} ...")
            ctx["evidence"] = (
                evidence.for_role(name) if (evidence is not None and name in EVIDENCE_ROLES)
                else NEUTRAL_EVIDENCE
            )
            user = template.safe_substitute(ctx)
            parsed = _parse_json(self._call(system, user))
            reviews[name] = parsed
            role_dims = parsed.get("dimensions", {}) if isinstance(parsed, dict) else {}
            for d in dims:
                entry = role_dims.get(d, {})
                val = entry.get("score") if isinstance(entry, dict) else None
                try:
                    scores[d] = float(val) if val is not None else None
                except (TypeError, ValueError):
                    scores[d] = None

        # ── Structured signals gathered from the specialists ──
        def _rev(role, key):
            return reviews.get(role, {}).get(key, []) or []

        challenge_coverage = _rev("method_feasibility", "challenge_coverage")
        requirements_coverage = _rev("kpi_requirements", "requirements_coverage")
        section_checklist = _rev("compliance_writing", "section_checklist")
        constraint_violations = _rev("compliance_writing", "constraint_violations")
        over_claims = _rev("science_innovation", "over_claims")
        empty_phrases = _rev("kpi_requirements", "empty_phrases")

        has_uncovered_requirement = any(
            str(c.get("level", "")).lower() == "none" for c in requirements_coverage if isinstance(c, dict)
        )
        has_missing_section = any(
            c.get("present") is False for c in section_checklist if isinstance(c, dict)
        )

        # ── Code-side aggregation & verdict ──
        overall = weighted_overall(scores)
        verdict = decide_verdict(overall, has_uncovered_requirement, has_missing_section)

        # ── Chair: qualitative consolidation only ──
        final_scores = {k: scores[k] for k in WEIGHTS}
        chair_user = CHAIR_USER.safe_substitute(
            title=ctx["title"],
            reviews_json=json.dumps(reviews, ensure_ascii=False),
            scores_json=json.dumps(final_scores, ensure_ascii=False),
            overall_score=overall,
            verdict=verdict,
        )
        chair = _parse_json(self._call(CHAIR_SYSTEM, chair_user))

        return PanelResult(
            scores={k: (v if v is not None else 0.0) for k, v in scores.items()},
            overall_score=overall,
            strengths=chair.get("strengths", []) if isinstance(chair, dict) else [],
            weaknesses=chair.get("weaknesses", []) if isinstance(chair, dict) else [],
            verdict=verdict,
            summary=chair.get("summary", "") if isinstance(chair, dict) else "",
            reviews=reviews,
            challenge_coverage=challenge_coverage,
            requirements_coverage=requirements_coverage,
            section_checklist=section_checklist,
            constraint_violations=constraint_violations,
            over_claims=over_claims,
            empty_phrases=empty_phrases,
            raw_response=json.dumps(chair, ensure_ascii=False),
        )
