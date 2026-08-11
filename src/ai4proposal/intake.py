"""Guideline intake — a funding call document in, a pipeline-ready task out.

Until now every `cases/tasks/*.json` was transcribed by hand. This module closes
that gap so a user can hand over the call document itself.

The work is split into separate LLM calls rather than one big extraction,
because a person needs to intervene in the middle: a call typically offers
several research directions and names no specific topic, so the direction and
the topic are *choices*, not facts to be extracted. The stages are:

    extract_text     .docx / .txt / .md  ->  plain text
    parse_call       text               ->  structured call (facts only)
    pick_direction   call               ->  one direction, when the user defers
    propose_topics   call + direction   ->  candidate topics (the invented part)
    build_task       call + direction + topic -> task dict the pipeline accepts

`parse_call` is deliberately conservative: it transcribes what the document
says and leaves anything absent as null rather than inventing it. Everything
invented lives in `propose_topics`, and `build_task` records that split in the
task's `provenance`, matching how the hand-written cases are labelled.
"""
from __future__ import annotations

import html
import json
import re
import zipfile
from io import BytesIO
from typing import Any, Dict, List, Optional

MAX_GUIDELINE_CHARS = 20000


# ═══════════════════════════════ text extraction ═══════════════════════════════

_W_PARA = re.compile(r"<w:p\b.*?</w:p>|<w:p\b[^>]*/>", re.S)
_W_TAB = re.compile(r"<w:tab[^>]*/>")
_W_BR = re.compile(r"<w:br[^>]*/>")
_TAGS = re.compile(r"<[^>]+>")


def _docx_text(data: bytes) -> str:
    """Paragraph text of a .docx, without a python-docx dependency."""
    with zipfile.ZipFile(BytesIO(data)) as z:
        xml = z.read("word/document.xml").decode("utf-8", errors="ignore")
    lines = []
    for para in _W_PARA.findall(xml):
        para = _W_TAB.sub("\t", _W_BR.sub("\n", para))
        text = html.unescape(_TAGS.sub("", para)).strip()
        if text:
            lines.append(text)
    return "\n".join(lines)


def extract_text(filename: str, data: bytes) -> str:
    """Plain text of an uploaded call document.

    Raises ValueError on an unsupported type — the caller should surface that to
    the user rather than feeding an empty string to the model.
    """
    name = (filename or "").lower()
    if name.endswith(".docx"):
        text = _docx_text(data)
    elif name.endswith((".txt", ".md", ".markdown")):
        text = data.decode("utf-8", errors="ignore")
    elif name.endswith(".doc"):
        raise ValueError("旧版 .doc 不受支持，请先另存为 .docx")
    elif name.endswith(".pdf"):
        raise ValueError("PDF 暂不支持，请转存为 .docx 或纯文本后上传")
    else:
        raise ValueError(f"不支持的文件类型：{filename}（支持 .docx / .txt / .md）")
    text = text.strip()
    if not text:
        raise ValueError("未能从文件中提取到任何文字，请确认文件内容")
    return text


# ═══════════════════════════════ call parsing ═══════════════════════════════

PARSE_SYSTEM = """你是科研项目管理专家，负责把资助指南原文转写为结构化数据，供后续系统使用。

**你的唯一职责是忠实转写，不是创作。** 指南里写了什么就记什么；指南没写的一律留空（null 或空数组），
**绝对不要根据常识补全、推测或润色**。后续环节会明确区分"来自指南"与"人为构造"两部分，
你这一步的输出若混入臆测，会污染整条链路。

只输出 JSON，不要 markdown 代码块。"""

PARSE_USER = """请把下面的资助指南转写为结构化 JSON。

## 关键区分
- **requirements（硬性交付物）**：项目**必须产出**的东西，可数、可验收，能回答"交付了几个/几项"。
  例："新增申请发明专利不少于1项""开发不少于5个算子并开源至指定仓库"
- **constraints（硬性约束）**：对项目**形式、范围或呈现方式**的限定，**本身不是产出物**。
  例："课题周期1年""创新范围须为指南所列方向之一""发表文章须提及某语言""成果须标注为基于某平台"

  判据：若一句话规定的是"产出什么"，归 requirements；若规定的是"产出必须满足什么条件、
  必须怎么标注、必须落在什么范围内"，归 constraints。
  **原文用分号并列的一长句，可能同时包含两类，必须拆开分别归类**，不要整句塞进一边。
- **directions（可选方向）**：指南列出的**并列研究方向**。这类计划通常只圈定方向、
  不指定具体课题，申请人自行选题。把每个方向单列一条，`detail` 保留该方向下列举的具体技术点。
  若指南只有一个方向，也放进数组（长度为 1）。
- **structure（行文结构）**：仅当指南**明确规定**了申请书须包含哪些章节时才填；
  只是提了一句"须包含研究内容"不算规定结构，此时填 null。

## 指南原文
${guideline_text}

只输出：
{
  "program": "资助计划名称，含方向编号等限定语",
  "sponsor": "资助方或组织方",
  "language": "zh 或 en，指申请书应使用的语言",
  "budget": {"amount": 数字或null, "currency": "CNY|USD|GBP…", "dur": 月数或null,
             "is_cap": true/false, "note": "原文关于经费的表述"},
  // amount 必须换算成**基本单位的纯数字**：中文"200万"→2000000，"50万元"→500000，
  // 英文"£3M"→3000000。原文写了金额就必须给出数字，不得留 null；有多档时取最高档，
  // 并在 note 中说明取的是哪一档。dur 换算成月数："周期1年"→12。
  "eligibility": "申请资格要求；未写则空字符串",
  "requirements": ["硬性交付物，逐条，保留原文数量词"],
  "constraints": ["硬性约束，逐条"],
  "directions": [{"id": "d1", "name": "方向名称", "detail": "该方向下列举的具体技术点或范围"}],
  "structure": null 或 {"template": "模板名称",
                        "core_sections": [{"id": "英文短标识", "name": "章节名",
                                           "required": ["该章必备要素"], "word_limit": 数字或null}],
                        "rules": ["行文规则"]},
  "uncertain": ["转写过程中不确定或原文未明确的地方，供用户核对；没有则空数组"]
}"""


def parse_call(llm: Any, guideline_text: str) -> Dict[str, Any]:
    """Transcribe a call document into structured fields. Facts only."""
    text = guideline_text[:MAX_GUIDELINE_CHARS]
    raw = llm.generate_text(
        system_prompt=PARSE_SYSTEM,
        user_prompt=PARSE_USER.replace("${guideline_text}", text),
    )
    data = _parse_json(raw)
    if not isinstance(data, dict) or not data.get("program"):
        raise ValueError("指南解析失败：模型未返回可用的结构化结果，请重试或检查文件内容")

    # The model returns explicit null for anything the document does not state —
    # that is the behaviour we want, but downstream code and the UI expect strings.
    for key in ("sponsor", "language", "eligibility"):
        data[key] = str(data.get(key) or "").strip()
    data["language"] = data["language"] or "zh"
    for key in ("requirements", "constraints", "uncertain"):
        if not isinstance(data.get(key), list):
            data[key] = []
        # str(None) is the truthy "None", so drop null entries before stringifying
        data[key] = [str(x).strip() for x in data[key] if x is not None and str(x).strip()]
    dirs = data.get("directions")
    if not isinstance(dirs, list) or not dirs:
        # A call with no enumerated directions still needs one to choose from.
        dirs = [{"id": "d1", "name": data.get("program", "本计划"), "detail": ""}]
    for i, d in enumerate(dirs, 1):
        d.setdefault("id", f"d{i}")
        d.setdefault("name", "")
        d.setdefault("detail", "")
    data["directions"] = dirs
    if not isinstance(data.get("budget"), dict):
        data["budget"] = {"amount": None, "currency": "", "dur": None,
                          "is_cap": False, "note": ""}
    if not isinstance(data.get("structure"), dict):
        data["structure"] = None
    return data


# ═══════════════════════════════ direction choice ═══════════════════════════════

PICK_SYSTEM = """你是科研战略顾问。用户上传了一份资助指南，但没有指定研究方向，希望你替他选。
你的任务是在指南列出的方向中挑一个最值得投的，并说明理由。只输出 JSON。"""

PICK_USER = """## 资助计划
${program}

## 可选方向
${directions}

## 硬性交付物（会影响哪个方向更容易达成）
${requirements}

## 选择标准
- 该方向是否与硬性交付物天然契合（例如要求"开源算子"，则系统软件类方向比纯理论方向更容易落地）
- 该方向当前是否有明确的技术缺口、值得投入
- 避免选择过于宽泛、难以在给定周期内形成可验收成果的方向

只输出：{"id": "所选方向的 id", "reason": "80字以内，说明为何选它"}"""


def pick_direction(llm: Any, call: Dict[str, Any]) -> Dict[str, Any]:
    """Choose a direction on the user's behalf. Falls back to the first one."""
    dirs = call.get("directions") or []
    if not dirs:
        raise ValueError("该指南没有可选方向")
    if len(dirs) == 1:
        return {**dirs[0], "reason": "指南只列出这一个方向"}

    listing = "\n".join(f"- [{d['id']}] {d['name']}：{d['detail']}" for d in dirs)
    reqs = "\n".join(f"- {r}" for r in call.get("requirements", [])) or "（未列明）"
    try:
        raw = llm.generate_text(
            system_prompt=PICK_SYSTEM,
            user_prompt=(PICK_USER.replace("${program}", call.get("program", ""))
                                  .replace("${directions}", listing)
                                  .replace("${requirements}", reqs)))
        data = _parse_json(raw)
        chosen = next((d for d in dirs if d["id"] == data.get("id")), None)
        if chosen:
            return {**chosen, "reason": data.get("reason", "")}
    except Exception:
        pass
    return {**dirs[0], "reason": "自动选择失败，回退到第一个方向"}


# ═══════════════════════════════ topic proposal ═══════════════════════════════

TOPIC_SYSTEM = """你是资深科研项目申请人。给定一份资助指南和其中一个研究方向，你要提出若干个具体的候选课题。

指南通常只圈定方向、不指定课题，**选题是申请人的工作**——这一步是整条链路中唯一允许创作的环节。

好的候选课题应满足：
- 落在所选方向范围之内，不越界到其他方向
- 能自然承接指南的全部硬性交付物（若要求开源算子，课题就该真的产出算子）
- 有明确的技术缺口，而非"某某技术研究"这类无边界的题目
- 在指南规定的周期与经费内可完成

只输出 JSON，不要 markdown 代码块。"""

TOPIC_USER = """## 资助计划
${program}

## 选定方向
${direction_name}：${direction_detail}

## 硬性交付物
${requirements}

## 硬性约束
${constraints}

## 要求
提出 ${n} 个**彼此有实质差异**的候选课题（不是同一想法的不同措辞）。每个给出：

- title：课题名称，具体到能看出做什么，不用"研究""探索"开头的空泛句式
- domain：领域标识，英文小写下划线，如 ai_systems / synthetic_biology / social_science
- background：立项依据 250-400 字。写清楚**当前存在什么技术缺口、为何现有做法不足**，
  最后一句点明本课题的切入点。不要写成综述，不要堆砌形容词。
- challenges：3-4 条该课题需要突破的**开放问题**（不是研究计划，是难点本身）
- fit：一句话说明它如何承接上面的硬性交付物

**不要编造任何具体数值、机构名、已有成果或前期基础**——这些在后续环节由申请人填写。

只输出：
{"topics": [{"title": "...", "domain": "...", "background": "...",
             "challenges": ["...", "..."], "fit": "..."}]}"""


def propose_topics(llm: Any, call: Dict[str, Any], direction: Dict[str, Any],
                   n: int = 3) -> List[Dict[str, Any]]:
    """Propose candidate topics under a chosen direction. This is the one stage
    that invents rather than transcribes."""
    user = (TOPIC_USER
            .replace("${program}", call.get("program", ""))
            .replace("${direction_name}", direction.get("name", ""))
            .replace("${direction_detail}", direction.get("detail", ""))
            .replace("${requirements}", "\n".join(f"- {r}" for r in call.get("requirements", [])) or "（未列明）")
            .replace("${constraints}", "\n".join(f"- {c}" for c in call.get("constraints", [])) or "（未列明）")
            .replace("${n}", str(n)))
    raw = llm.generate_text(system_prompt=TOPIC_SYSTEM, user_prompt=user)
    data = _parse_json(raw)
    topics = data.get("topics") if isinstance(data, dict) else None
    if not isinstance(topics, list) or not topics:
        raise ValueError("选题生成失败：模型未返回候选课题，请重试")

    out = []
    for t in topics[:n]:
        if not isinstance(t, dict) or not t.get("title"):
            continue
        out.append({
            "title": str(t.get("title", "")).strip(),
            "domain": str(t.get("domain", "") or "general").strip(),
            "background": str(t.get("background", "")).strip(),
            "challenges": [str(c).strip() for c in (t.get("challenges") or []) if str(c).strip()],
            "fit": str(t.get("fit", "")).strip(),
        })
    if not out:
        raise ValueError("选题生成失败：返回的候选课题格式不可用")
    return out


# ═══════════════════════════════ task assembly ═══════════════════════════════

def build_task(call: Dict[str, Any], direction: Dict[str, Any],
               topic: Dict[str, Any], task_id: str = "task_web") -> Dict[str, Any]:
    """Assemble the task dict `run_pipeline` and `evaluation` consume.

    `structure` is passed through only when the call actually mandated one;
    otherwise it is omitted so the pipeline's STRUCTURE_PLANNER designs a layout
    for this specific call rather than a hardcoded default being assumed.
    """
    direction_text = direction.get("name", "")
    if direction.get("detail"):
        direction_text = f"{direction_text}：{direction['detail']}"

    # A call that offers several directions carries a constraint like "创新范围须为
    # 指南所列方向之一". Left generic, a judge cannot check it — it is satisfiable by
    # any proposal. Naming the chosen direction turns it into something verifiable,
    # which is what the hand-written cases do.
    constraints = list(call.get("constraints", []))
    scope_words = ("方向之一", "范围须为", "限于以下方向", "one of the", "must fall within")
    for i, c in enumerate(constraints):
        if any(w in c for w in scope_words) and direction_text and direction_text not in c:
            constraints[i] = f"{c}（本课题选定：{direction_text}）"
            break

    task: Dict[str, Any] = {
        "task_id": task_id,
        "program": call.get("program", ""),
        "direction": direction_text,
        "title": topic.get("title", ""),
        "domain": topic.get("domain", "general"),
        "language": call.get("language", "zh"),
        "background": topic.get("background", ""),
        "challenges": topic.get("challenges", []),
        "references": [],
        "sponsor": call.get("sponsor", ""),
        "budget": call.get("budget", {}),
        "eligibility": call.get("eligibility", ""),
        "requirements": call.get("requirements", []),
        "constraints": constraints,
        "provenance": {
            "origin_type": "uploaded_guideline_plus_generated_topic",
            "basis_urls": [],
            "note": ("program/sponsor/budget/eligibility/requirements/constraints/structure "
                     "转写自用户上传的指南原文；direction 为用户在指南所列方向中的选择；"
                     "title/background/challenges 由系统在该方向下生成，非指南原文。"),
        },
    }
    if isinstance(call.get("structure"), dict) and call["structure"].get("core_sections"):
        task["structure"] = call["structure"]
    return task


# ═══════════════════════════════ helpers ═══════════════════════════════

def _parse_json(text: str) -> Dict[str, Any]:
    """Best-effort JSON extraction from an LLM reply."""
    if not text:
        return {}
    try:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(text[start:end])
    except Exception:
        pass
    return {}
