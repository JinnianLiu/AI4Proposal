"""Rubric-based panel evaluation of a proposal Markdown document.

Design decisions (agreed 2026-08):

* **No gold comparison.** Scoring is against written rubrics only, so a proposal
  can be judged without a matching winning proposal existing. Every dimension
  carries five explicit anchor bands; the LLM picks a band, it does not invent a
  scale.
* **Four specialist judges + a chair.** Each judge reads the FULL document
  independently and owns one or more of the seven dimensions. Judges never see
  each other's verdicts, so their scores stay independent.
* **The code computes the overall score**, never the LLM: judges emit per-dimension
  scores, `weighted_overall` aggregates them, `decide_verdict` applies hard gates.
* **Scored on core content only.** The writing pipeline produces 目标/考核指标/
  研发内容/技术方案/预期成果 — not team, budget, facilities or references. The
  feasibility and compliance rubrics are scoped to match, and explicitly instruct
  judges NOT to penalise those absent sections.
* **Output is a score**, not writer feedback — nothing here feeds back into generation.

External retrieval is injected into the science judge only (`scientific_quality`
/ `innovation`), via `evidence.EvidencePack`. It supplies evidence, never a
verdict, and degrades to a neutral placeholder when retrieval is unavailable.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from string import Template
from typing import Any, Dict, List, Optional

from .evidence import NEUTRAL_EVIDENCE

MAX_PROPOSAL_CHARS = 24000

# ── Aggregation weights (code-computed overall). Tunable; must sum to 1.00 ──
# Rationale: what a panel actually decides on — the science and its novelty —
# carries the most weight; execution realism next; presentation least.
WEIGHTS: Dict[str, float] = {
    "scientific_quality": 0.20,
    "innovation":         0.18,
    "feasibility":        0.16,
    "alignment":          0.14,
    "impact":             0.12,
    "compliance":         0.11,
    "clarity":            0.09,
}

DIMENSION_NAMES: Dict[str, str] = {
    "scientific_quality": "科学质量",
    "feasibility":        "可行性",
    "innovation":         "创新性",
    "clarity":            "清晰度",
    "compliance":         "规范性/合规性",
    "impact":             "学术影响/意义",
    "alignment":          "一致性/契合度",
}

# Fallback expected sections when a task declares no structure.
DEFAULT_CORE_SECTIONS = [
    "课题目标", "考核指标", "主要研发内容、关键技术及创新点",
    "技术方案、技术路线及计划进度", "项目成果及推广措施",
]

# Sections the pipeline does not generate — judges must not deduct for their absence.
OUT_OF_SCOPE_NOTE = """## 评分范围限定（重要）
本文档只包含申请书的**核心研究内容**。团队构成、研究基础、经费预算、设备条件、
参考文献列表**不在本文档的生成范围内**，其缺失是既定设计，**不得作为任何维度的扣分理由**，
也不得在 weaknesses 中提出。只评价文档中实际存在的内容。"""


# ═══════════════════════════════ Rubrics ═══════════════════════════════
# Five anchor bands per dimension. These ARE the scoring standard — a judge
# selects the band its reading matches rather than scoring on intuition.

RUBRICS: Dict[str, str] = {
    "scientific_quality": """scientific_quality（科学质量）：科学问题是否明确、重要、可证伪；是否具有机制、理论或规律层面的研究价值；假设与验证是否成立。
  9-10 科学问题明确到可证伪的程度，给出了具体假设与相应的验证方式；研究触及机制/理论/规律层面而非仅实现某功能；假设—验证链条完整，并交代了何种结果可推翻假设
  7-8  科学问题清晰且重要，有机制层面的追问；但假设与验证方式的对应关系部分需靠读者推断
  5-6  问题偏向工程实现或表述为"研究某某技术"而非可证伪的科学问题；验证方式笼统（如"通过实验验证有效性"）
  3-4  只有任务描述与技术堆叠，看不出科学问题；无可识别的假设
  1-2  无法识别出研究问题""",

    "feasibility": """feasibility（可行性）：技术路线、实验设计、数据、周期与指标可达性是否支撑项目完成。
  9-10 技术路线分解到可执行步骤，每步交代输入、输出与判定依据；数据/实验设计具体（来源、规模、对照设置）；周期与里程碑同工作量匹配；关键指标给出可达性论证；有风险识别与备选方案
  7-8  路线完整、阶段划分合理，但个别环节的可达性论证或数据来源交代不足
  5-6  路线停留在框架层面（"设计…构建…验证…"），缺少判定依据；进度安排与工作量是否匹配存疑
  3-4  路线与目标脱节，或明显低估工作量；无风险考虑
  1-2  给不出可执行的路径
  注：团队、经费、设备、算力条件不在本文档范围，不得据此扣分。""",

    "innovation": """innovation（创新性）：是否提出新机制、新理论、新方法或新规律；是否超越已有工作的简单组合、调参与工程集成。
  9-10 明确给出"现有做法 → 本课题做法"的差异，且差异在机制/理论/方法层面成立；创新点可被独立复述而不依赖修饰语
  7-8  有真实新意，但主要是已知方法在新场景下的有效组合
  5-6  增量改进、参数调优或工程集成；创新性主要靠形容词（"首创""突破""国际领先"）支撑
  3-4  以新名词包装已有工作
  1-2  无创新，或与已有工作无法区分
  注：外部检索证据仅供参考，判断权在你。**检索不到 ≠ 不存在**——文献库对最近 1-2 年的成果、
      新发布的模型/系统名称、预印本与工业界发布收录严重滞后。因此不得仅凭"检索无结果"就判定
      某项工作或某个模型名称属于虚构或过度声称；只有当检索**明确检出**了在先工作、而正文仍声称
      "首次/填补空白"时，才构成过度声称。""",

    "clarity": """clarity（清晰度）：表达是否清楚；概念、变量、术语、任务编号、指标、时间表是否明确且前后一致。
  9-10 概念/变量/术语首次出现即定义且全文一致；任务编号与指标编号可相互索引；时间表与研究内容对应无歧义
  7-8  整体清楚，个别术语或编号存在前后不一致
  5-6  存在指代不明、术语漂移、编号断裂，或时间表与研究内容对不上
  3-4  需反复回读才能理解；关键定义缺失
  1-2  表述混乱，无法稳定理解其主张
  注：只评价**文本自身**是否清楚一致。正文主题与课题标题是否吻合由"一致性/契合度"维度负责；
      即使本文写的是另一个课题，只要它自身表述清楚一致，本维度仍应给高分。""",

    "compliance": """compliance（规范性/合规性）：是否符合项目书基本结构与规范；是否满足指南列明的硬性约束；是否存在占位符或不规范内容。
  9-10 覆盖指南要求的全部章节与要素；**逐条满足"指南硬性约束"**；无占位符、无"待补充/TBD/xxx/【】"一类残留；外文名词首次出现给出全称与缩写；字数限制满足
  7-8  结构完整、无约束违反，但个别要素缺失或字数轻微越界
  5-6  缺少某个被要求的章节或要素；或有 1 条硬性约束未落实；或存在少量占位符
  3-4  多个必需要素缺失；或有 2 条及以上硬性约束未落实；或存在大段套话式模板内容
  1-2  结构不成立
  注1：团队、经费预算、参考文献列表不在本文档生成范围，其缺失不得扣分。
  注2：你在 constraint_violations 中列出的每一条，都必须在本维度的档位选择中体现——
       列出了违反项却仍给 9-10 分，属于自相矛盾。
  注3：正文主题是否契合课题标题/指南方向，**不由本维度评价**，不得因跑题而压低本维度。""",

    "impact": """impact（学术影响/意义）：科学问题的重要性、预期理论贡献、潜在学术影响及应用价值是否可信且可说明。
  9-10 重要性论述有依据而非"具有重要意义"式空话；预期理论贡献具体到改变了什么认知或能力；应用价值有明确的承接对象或场景
  7-8  意义论述成立，但部分依赖领域共识而非本课题特有的贡献
  5-6  意义论述空泛，整段可原样套用到同领域任意课题
  3-4  仅有口号式表述
  1-2  无法说明为什么这项工作值得做""",

    "alignment": """alignment（一致性/契合度）：是否契合项目题目与指南主题；科学问题、目标、内容、路线、创新点、成果之间是否形成闭环。
  9-10 题目/指南主题/科学问题/目标/内容/路线/创新点/成果形成闭环，每个目标都能追溯到对应的研究内容、技术路线与产出成果，无孤立项
  7-8  主体闭环，个别成果或创新点找不到对应的研究内容
  5-6  模块平铺，章节之间靠标题串联而非逻辑承接；存在偏离主题的段落
  3-4  目标与研究内容明显不对应，或偏离指南限定的方向
  1-2  与题目或指南主题无关""",
}


# ═══════════════════════════ Shared prompt scaffold ═══════════════════════════

_COMMON_HEADER = Template("""## 课题信息
标题：${title}
资助计划：${program}
背景/立项依据：${background}

## 指南硬性要求
${requirements}

## 指南硬性约束
${constraints}

## 本方向的开放挑战
${challenges}

## 期望章节
${sections}

""" + OUT_OF_SCOPE_NOTE + """

## 待评审正文（Markdown 全文）
${proposal_text}
""")

_SCORING_RULES = """## 评分要求
- 严格对照上面的分档锚点选择档位，不要凭印象给分。先写 reason（引用正文中的具体表述作为依据），再给 score。
- reason 必须指向正文里的具体内容；不接受"整体较好""有待加强"这类无指向的评语。
- 分数为 1-10 的整数。同一维度不同本子之间必须可比。
- 只输出 JSON，不要 markdown 代码块，不要任何解释性文字。

## 维度隔离（必须遵守）
你**只对上面列出的维度负责**。评审组另有专家分别负责科学质量、创新性、可行性、
学术影响、一致性/契合度、清晰度、规范性中你不负责的部分，总分由系统按权重合成。

因此：**不属于你所负责维度的缺陷，一律不得影响你的分数。** 尤其是——
- 正文主题是否契合课题标题与指南方向，由"一致性/契合度"维度的专家负责。
  即使你认为本文严重跑题，也**不得**因此压低你所负责的任何维度；请只就你的维度评价文中实际写出的内容。
- 反过来，也不要因为某一维度表现突出就抬高其他维度。
把你负责的维度当作独立量表来打分，就像这份文档在其他方面都合格一样。

## 9-10 档的使用
9-10 档表示"该维度可作为同类申请书的范例"，应当罕见。若你只是觉得"写得不错、
没有明显问题"，那是 7-8 档。只有当正文提供了锚点所要求的**全部**要素时才给 9-10。"""


def _judge_prompt(intro: str, dims: List[str], extra_task: str, schema: str) -> Template:
    """Assemble a specialist prompt: intro + shared context + its rubrics + schema."""
    rubric_block = "\n".join(RUBRICS[d] for d in dims)
    return Template(
        intro + "\n\n" + _COMMON_HEADER.template
        + "\n## 评分维度与分档锚点（1-10）\n" + rubric_block
        + ("\n\n" + extra_task if extra_task else "")
        + "\n\n" + _SCORING_RULES
        + "\n\n只输出：\n" + schema
    )


def _dim_schema(role: str, dims: List[str], extra: str = "") -> str:
    body = ",\n".join(f'    "{d}": {{"reason": "...", "score": 0}}' for d in dims)
    return ('{\n  "role": "%s",\n  "dimensions": {\n%s\n  }%s\n}'
            % (role, body, ("," + extra) if extra else ""))


# ══════════════════════════════ Judge definitions ══════════════════════════════

SCIENCE_SYSTEM = """你是本资助计划的资深评审专家，专精"科学质量与创新性"的判断。你通读申请书核心内容全文，只对科学质量与创新性负责，不评价文字表达或格式。

你严格区分真实的机制/理论/方法创新与用华丽措辞包装的增量工作，并主动识别过度声称（如"首次""可证明""填补空白""国际领先"却无支撑）。评分先写依据后给分。只输出 JSON。"""

SCIENCE_USER = _judge_prompt(
    "评审以下申请书核心内容的【科学质量】与【创新性】。",
    ["scientific_quality", "innovation"],
    """## 外部文献证据（用于核查创新性与过度声称；judge 拥有最终判断权，勿被检索结果直接左右）
${evidence}

## 附加任务
- 列出识别到的过度声称 over_claims（原文表述 + 为何缺乏支撑），无则空数组。
- 列出正文中明确可辨的科学假设 hypotheses，无则空数组。""",
    _dim_schema("science", ["scientific_quality", "innovation"],
                '\n  "over_claims": ["..."],\n  "hypotheses": ["..."]'),
)

VALUE_SYSTEM = """你是本资助计划的资深评审专家，专精"研究意义与整体契合度"的判断。你通读申请书核心内容全文，只对学术影响与一致性负责。

你警惕可以原样套用到任意课题的空泛意义论述，并逐条核对目标—内容—路线—创新点—成果之间是否真的闭环。评分先写依据后给分。只输出 JSON。"""

VALUE_USER = _judge_prompt(
    "评审以下申请书核心内容的【学术影响/意义】与【一致性/契合度】。",
    ["impact", "alignment"],
    """## 附加任务
- 逐条核对"指南硬性要求"是否在正文中得到落实，输出 requirement_coverage：
  每条给 {"requirement": "...", "covered": true/false, "where": "正文中的对应表述或章节，未覆盖则填空字符串"}。
- 列出与题目或指南方向不符的跑题内容 off_topic，无则空数组。""",
    _dim_schema("value", ["impact", "alignment"],
                '\n  "requirement_coverage": [{"requirement": "...", "covered": true, "where": "..."}],'
                '\n  "off_topic": ["..."]'),
)

FEASIBILITY_SYSTEM = """你是本资助计划的资深评审专家，专精"可行性"判断。你通读申请书核心内容全文，只对可行性负责，不评价创新性或文字表达。

你关注技术路线能否真正执行、指标能否真正达到、周期与工作量是否匹配。你对"设计…构建…验证…"这类无判定依据的框架式路线保持警惕。评分先写依据后给分。只输出 JSON。"""

FEASIBILITY_USER = _judge_prompt(
    "评审以下申请书核心内容的【可行性】。",
    ["feasibility"],
    """## 附加任务
- 列出你认为难以达成或缺乏可达性论证的指标 risky_targets（指标原文 + 存疑理由），无则空数组。
- 列出正文中已给出的风险应对/备选方案 mitigations，无则空数组。""",
    _dim_schema("feasibility", ["feasibility"],
                '\n  "risky_targets": ["..."],\n  "mitigations": ["..."]'),
)

WRITING_SYSTEM = """你是本资助计划的形式审查专家，专精"表达清晰度与规范性"。你通读申请书核心内容全文，只对清晰度与规范性负责，不评价科学价值或创新性高低。

你逐项核对章节完整性、编号一致性、术语定义、占位符残留。评分先写依据后给分。只输出 JSON。"""

WRITING_USER = _judge_prompt(
    "评审以下申请书核心内容的【清晰度】与【规范性/合规性】。",
    ["clarity", "compliance"],
    """## 附加任务
- 对照"期望章节"逐节核对，输出 section_checklist：
  每节给 {"section": "...", "present": true/false, "note": "缺失或不完整之处，完整则填空字符串"}。
- 列出占位符/模板残留 placeholders（如"待补充""TBD""xxx""【】"及未填充的模板句），无则空数组。
- 列出违反"指南硬性约束"之处 constraint_violations，无则空数组。""",
    _dim_schema("writing", ["clarity", "compliance"],
                '\n  "section_checklist": [{"section": "...", "present": true, "note": "..."}],'
                '\n  "placeholders": ["..."],\n  "constraint_violations": ["..."]'),
)

CHAIR_SYSTEM = """你是评审组主席。你已收到各位专家的独立评分与依据，现在负责汇总定性意见。

你不重新打分，也不修改专家分数——总分由系统计算。你只负责综合出优点、缺点与总体评语。只输出 JSON。"""

CHAIR_USER = Template("""汇总以下专家评审意见。

## 课题
${title}

## 各专家评分与依据
${reviews}

## 系统计算的加权总分（供参考，不要改动）
${overall} / 10

## 要求
- strengths：4-6 条具体优点，每条须指向正文的具体内容，不接受空泛表扬。
- weaknesses：4-6 条具体问题，每条给出可操作的改进方向。
- summary：一段总体评语（150-250字），说明这份申请书最关键的长处与最致命的短板。
- 不得因团队、经费、参考文献等不在本文档范围的内容提出缺点。

只输出：
{
  "strengths": ["..."],
  "weaknesses": ["..."],
  "summary": "..."
}""")

# role → (system, user template, owned dimensions, needs external evidence)
JUDGES: List[tuple] = [
    ("science",     SCIENCE_SYSTEM,     SCIENCE_USER,     ["scientific_quality", "innovation"], True),
    ("value",       VALUE_SYSTEM,       VALUE_USER,       ["impact", "alignment"],              False),
    ("feasibility", FEASIBILITY_SYSTEM, FEASIBILITY_USER, ["feasibility"],                      False),
    ("writing",     WRITING_SYSTEM,     WRITING_USER,     ["clarity", "compliance"],            False),
]


# ═══════════════════════════════ Result & scoring ═══════════════════════════════

@dataclass
class EvaluationResult:
    scores: Dict[str, float]
    overall_score: float           # 1-10, weighted, computed in code
    overall_100: float             # same value rescaled to 0-100 for reporting
    verdict: str
    strengths: List[str] = field(default_factory=list)
    weaknesses: List[str] = field(default_factory=list)
    summary: str = ""
    reviews: Dict[str, Any] = field(default_factory=dict)
    over_claims: List[str] = field(default_factory=list)
    hypotheses: List[str] = field(default_factory=list)
    requirement_coverage: List[Dict[str, Any]] = field(default_factory=list)
    off_topic: List[str] = field(default_factory=list)
    risky_targets: List[str] = field(default_factory=list)
    mitigations: List[str] = field(default_factory=list)
    section_checklist: List[Dict[str, Any]] = field(default_factory=list)
    placeholders: List[str] = field(default_factory=list)
    constraint_violations: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        from dataclasses import asdict
        return asdict(self)


def weighted_overall(scores: Dict[str, Optional[float]]) -> float:
    """Weighted mean over the dimensions present; a missing dimension's weight is
    redistributed across the rest rather than counted as zero."""
    present = {k: v for k, v in scores.items() if v is not None and k in WEIGHTS}
    total_w = sum(WEIGHTS[k] for k in present)
    if total_w == 0:
        return 5.0
    return round(sum(present[k] * WEIGHTS[k] for k in present) / total_w, 2)


MIN_ALIGNMENT = 4.0   # below this the proposal is answering a different call


def decide_verdict(overall: float, has_uncovered_requirement: bool,
                   has_missing_section: bool, has_placeholder: bool,
                   alignment: Optional[float] = None) -> str:
    """Hard gates outrank the score.

    A proposal that does not address the call cannot be fixed by revision, so a
    failing `alignment` rejects outright however well the text scores elsewhere —
    dimension weighting alone let a wholly off-topic document reach 64/100. An
    unmet requirement, a missing mandated section or leftover placeholders are
    repairable, so they cap the verdict at revise_resubmit instead.
    """
    if alignment is not None and alignment < MIN_ALIGNMENT:
        return "reject"
    if overall < 6.0:
        return "reject"
    blocked = has_uncovered_requirement or has_missing_section or has_placeholder
    if overall >= 7.5 and not blocked:
        return "recommend_submit"
    return "revise_resubmit"


# ══════════════════════════════════ Helpers ══════════════════════════════════

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
    return "\n".join(f"{i + 1}. {c}" for i, c in enumerate(items))


def _sections_from_task(task: Dict[str, Any]) -> List[str]:
    st = task.get("structure") or {}
    names = [s.get("name", "") for s in (st.get("core_sections") or [])
             if isinstance(s, dict) and s.get("name")]
    return names or DEFAULT_CORE_SECTIONS


def _science_evidence(pack: Optional[Any]) -> str:
    """Evidence cards for the science judge, or a neutral placeholder."""
    if pack is None or pack.is_empty():
        return NEUTRAL_EVIDENCE
    return pack.format_cards() or NEUTRAL_EVIDENCE


def _clamp_score(raw: Any) -> Optional[float]:
    try:
        return max(1.0, min(10.0, float(raw)))
    except (TypeError, ValueError):
        return None


# ═══════════════════════════════ The panel ═══════════════════════════════

class RubricPanel:
    """Four rubric-driven specialist judges + a chair. Scores come from the judges,
    the overall score and verdict come from the code."""

    def __init__(self, llm: Any, max_retries: int = 2, verbose: bool = False):
        self.llm = llm
        self.max_retries = max_retries
        self.verbose = verbose

    def _call(self, system: str, user: str) -> str:
        last = ""
        for attempt in range(self.max_retries + 1):
            try:
                return self.llm.generate_text(system_prompt=system, user_prompt=user)
            except Exception as e:
                last = f"{type(e).__name__}: {e}"
                if self.verbose:
                    print(f"      [retry {attempt + 1}: {str(e)[:80]}]")
        raise RuntimeError(f"judge call failed after {self.max_retries + 1} tries: {last}")

    def evaluate(self, proposal_text: str, task: Dict[str, Any],
                 evidence: Optional[Any] = None) -> EvaluationResult:
        """`proposal_text` is the proposal Markdown; `task` is the full task dict."""
        if len(proposal_text) > MAX_PROPOSAL_CHARS:
            proposal_text = proposal_text[:MAX_PROPOSAL_CHARS] + "\n\n[... 截断 ...]"

        ctx = {
            "title": task.get("title", ""),
            "program": task.get("program", "") or task.get("sponsor", ""),
            "background": task.get("background", "") or task.get("abstract", ""),
            "requirements": _fmt_numbered(task.get("requirements", [])),
            "constraints": _fmt_numbered(task.get("constraints", [])),
            "challenges": _fmt_numbered(task.get("challenges", [])),
            "sections": _fmt_numbered(_sections_from_task(task)),
            "proposal_text": proposal_text,
            "evidence": _science_evidence(evidence),
        }

        scores: Dict[str, Optional[float]] = {}
        reviews: Dict[str, Any] = {}
        errors: List[str] = []

        for role, system, user_tpl, dims, _needs_ev in JUDGES:
            if self.verbose:
                print(f"    [judge] {role} → {', '.join(dims)}")
            try:
                raw = self._call(system, user_tpl.safe_substitute(ctx))
                data = _parse_json(raw)
            except Exception as e:
                errors.append(f"{role}: {e}")
                data = {}
            reviews[role] = data
            for d in dims:
                entry = (data.get("dimensions") or {}).get(d) or {}
                scores[d] = _clamp_score(entry.get("score"))
                if self.verbose:
                    got = scores[d]
                    print(f"      {d}: {got if got is not None else 'FAILED'}")

        overall = weighted_overall(scores)

        # structured findings, collected across judges
        sci, val = reviews.get("science", {}), reviews.get("value", {})
        fea, wri = reviews.get("feasibility", {}), reviews.get("writing", {})
        coverage = val.get("requirement_coverage") or []
        checklist = wri.get("section_checklist") or []
        placeholders = wri.get("placeholders") or []

        verdict = decide_verdict(
            overall,
            has_uncovered_requirement=any(not c.get("covered", True) for c in coverage),
            has_missing_section=any(not s.get("present", True) for s in checklist),
            has_placeholder=bool(placeholders),
            alignment=scores.get("alignment"),
        )

        chair = self._chair(task, reviews, overall, errors)

        return EvaluationResult(
            scores={k: v for k, v in scores.items() if v is not None},
            overall_score=overall,
            overall_100=round(overall * 10, 1),
            verdict=verdict,
            strengths=chair.get("strengths", []),
            weaknesses=chair.get("weaknesses", []),
            summary=chair.get("summary", ""),
            reviews=reviews,
            over_claims=sci.get("over_claims") or [],
            hypotheses=sci.get("hypotheses") or [],
            requirement_coverage=coverage,
            off_topic=val.get("off_topic") or [],
            risky_targets=fea.get("risky_targets") or [],
            mitigations=fea.get("mitigations") or [],
            section_checklist=checklist,
            placeholders=placeholders,
            constraint_violations=wri.get("constraint_violations") or [],
            errors=errors,
        )

    def _chair(self, task: Dict[str, Any], reviews: Dict[str, Any],
               overall: float, errors: List[str]) -> Dict[str, Any]:
        digest = []
        for role, data in reviews.items():
            for dim, entry in (data.get("dimensions") or {}).items():
                label = DIMENSION_NAMES.get(dim, dim)
                digest.append(f"[{role}] {label} {entry.get('score', '?')}/10 — "
                              f"{entry.get('reason', '')}")
        if not digest:
            return {"strengths": [], "weaknesses": [], "summary": "所有专家评审均失败，无法汇总。"}
        try:
            raw = self._call(CHAIR_SYSTEM, CHAIR_USER.safe_substitute(
                title=task.get("title", ""), reviews="\n".join(digest), overall=overall))
            return _parse_json(raw)
        except Exception as e:
            errors.append(f"chair: {e}")
            return {"strengths": [], "weaknesses": [], "summary": ""}
