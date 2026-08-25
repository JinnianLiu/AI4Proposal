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

# Raised from 20000, which showed only 27% of a Wellcome application form to
# parse_template: two sections that belong in the proposal (Research involving
# animals, Risks of research misuse) sat past the cut and were never seen.
MAX_GUIDELINE_CHARS = 60000


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
    elif name.endswith(".pdf"):
        text = _pdf_text(data)
    elif name.endswith((".txt", ".md", ".markdown")):
        text = data.decode("utf-8", errors="ignore")
    elif name.endswith(".doc"):
        raise ValueError("旧版 .doc 不受支持，请先另存为 .docx")
    else:
        raise ValueError(f"不支持的文件类型：{filename}（支持 .docx / .pdf / .txt / .md）")
    text = text.strip()
    if not text:
        raise ValueError("未能从文件中提取到文字。若是扫描件，请先做文字识别或改用可复制文本")
    return text


def _pdf_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ValueError("缺少 pypdf，无法读取 PDF") from exc
    reader = PdfReader(BytesIO(data))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


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
- **constraints（硬性约束）**：对**研究本身或其成果**的限定，**本身不是产出物**。
  例："课题周期1年""创新范围须为指南所列方向之一""发表文章须提及某语言""论证正文不超过4万字"

  判据：若一句话规定的是"产出什么"，归 requirements；若规定的是"研究或成果必须满足什么条件、
  必须落在什么范围内"，归 constraints。
  **原文用分号并列的一长句，可能同时包含两类，必须拆开分别归类**，不要整句塞进一边。

  **以下一律不要收录**（本系统只撰写申请书的核心研究内容，这些与撰写无关）：
  填表格式说明（如"封面某栏填写阿拉伯数字""按公章填写全称""关键词不超过3个"）、
  签字盖章与报送流程、表格页码与加页规则、人员编制与职称限制、
  经费预算编制办法、附件清单、审核意见栏的填写方式。
  判断方法：这条规定约束的是**研究内容本身**，还是**表格怎么填、材料怎么交**？后者一律丢弃。
- **directions（可选方向）**：指南列出的**并列研究方向**。这类计划通常只圈定方向、
  不指定具体课题，申请人自行选题。把每个方向单列一条，`detail` 保留该方向下列举的具体技术点。
  若指南只有一个方向，也放进数组（长度为 1）。
- **structure（行文结构）**：仅当指南**明确规定**了申请书须包含哪些章节时才填；
  只是提了一句"须包含研究内容"不算规定结构，此时填 null。

  **只保留属于"核心研究内容"的章节**：研究现状与选题价值、研究框架与目标、研究内容、
  研究方法与可行性、重点难点与创新、子课题结构、研究进度、预期成果一类。
  **必须排除**：数据表、学术简历、已发表成果目录、参考文献目录、经费预算表、
  各类承诺书与审核意见栏。若剔除后不剩几章，说明这份文件只是表格，structure 填 null。

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


# ═══════════════════════════ template & topic documents ═══════════════════════════
# A call is often split across several files: the call itself, a proposal template
# the funder wants filled in, and sometimes a topic the applicant has already
# settled on. Each is parsed by its own prompt and merged into the task.

TEMPLATE_SYSTEM = """你是科研项目管理专家。用户提供了一份申请书模板或提纲，你要把它转写为结构化的章节定义。

**只转写模板实际写明的章节**，不要补充你认为"应该有"的章节。模板里的填写说明、示例文字、
页眉页脚不是章节，不要当成章节。只输出 JSON。"""

TEMPLATE_USER = """把下面的申请书模板转写为章节结构。

## 只保留"核心研究内容"相关的章节
**排除**以下章节，它们不由本系统生成：数据表、学术简历、已承担项目与已发表成果目录、
**参考文献与研究资料目录**、经费预算表、团队组成与分工表、知识产权归属、附件清单、
各类承诺书与审核意见栏。
判据：这一章要写的是**本课题的研究内容**，还是**申请人的既往情况、钱、人、或行政手续**？后者排除。

## 摘要表与正文重复时，只保留正文章节
很多模板（名字里常带"含课题情况简表""基本情况表""申请书摘要表"）在正文之前先放一张**摘要表**，
表里的栏目名（如"课题目标""主要成果""考核指标""成果推广"）与后面的正文章节是**同一批内容的两套写法**，
摘要表是正文的缩写版。

这种情况下：
- **只输出正文章节，不要把摘要表的栏目也列成章节**——否则同一部分内容会被撰写两遍。
- 摘要表栏目若提到了正文章节没写明的要素，把该要素**并入**语义对应的正文章节的 `required`。
- 判断哪边是正文：篇幅更长、有独立标题层级、填写说明更详细的那一套是正文；被排在最前、
  以表格形式逐栏罗列、每栏只留几行的那一套是摘要表。
- 若整份模板**只有**摘要表而没有正文（确实有这种纯表格模板），则照常转写摘要表的栏目。

## 章节顺序
`core_sections` **必须保持模板原文的先后顺序**，不要按你认为更合理的逻辑重排——
资助方期望的就是模板的顺序。

## 模板原文
${template_text}

只输出：
{
  "template": "模板名称",
  "core_sections": [
    {"id": "英文小写短标识，如 objectives", "name": "章节名（用模板原文的写法）",
     "required": ["该章要求写明的要素，逐条；模板未写明则空数组"],
     "word_limit": 数字或null}
  ],
  "rules": ["模板规定的行文规则，如字数、编号方式；没有则空数组"]
}"""

TOPIC_DOC_SYSTEM = """你是科研项目管理专家。用户已经确定了课题选题，并提供了描述该选题的文档。
你要把它转写为结构化的选题信息。忠实转写，不要替用户重新构思选题。只输出 JSON。"""

TOPIC_DOC_USER = """把下面的选题文档转写为结构化选题。

## 文档原文
${topic_text}

## 说明
- title：课题名称。文档若有明确标题就用它，没有则从内容中概括一个。
- background：立项依据。用文档中的论述，可精简但不得改变原意；文档未提供则留空字符串。
- challenges：该课题的关键难点/开放问题。文档未列出则返回空数组，**不要替用户编造**。

只输出：{"title": "...", "domain": "英文小写领域标识", "background": "...", "challenges": ["..."]}"""


# Two names for one chapter differ by decoration, not by content:「预期成果及推广、
# 转化措施」vs「项目（课题）成果及推广措施」. Normalising the string is too brittle to
# see that (转化 alone breaks equality), so each name is reduced to the set of
# section concepts it mentions instead.
_SECTION_CONCEPTS = (
    ("目标", ("目标", "objective", "aim", "vision")),
    ("指标", ("考核指标", "验收指标", "指标", "kpi", "milestone" "指标值")),
    ("内容", ("研究内容", "研发内容", "内容", "content", "workplan")),
    ("创新", ("创新", "novel")),
    ("方案", ("技术方案", "方案", "设计", "design", "method")),
    ("路线", ("技术路线", "路线", "route", "pathway")),
    ("进度", ("进度", "里程碑", "计划", "schedule", "timeline")),
    ("成果", ("成果", "产出", "outcome", "output", "deliverable")),
    ("推广", ("推广", "转化", "应用", "dissemination", "translation", "impact")),
)


def _section_key(name: str) -> frozenset:
    """The set of section concepts a name mentions, for spotting the
    summary-table twin of a body chapter after the prompt failed to merge them."""
    low = str(name or "").lower()
    return frozenset(tag for tag, words in _SECTION_CONCEPTS if any(w in low for w in words))


def _dedupe_sections(sections: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Merge sections covering the same set of concepts, keeping the FIRST
    occurrence's position and the richest content.

    Only exact concept-set equality merges. Subset merging was tried and
    rejected: a template that legitimately separates 课题目标 from 考核指标 would
    have had one folded into the other. This is a narrow backstop — the primary
    fix is the summary-table rule in TEMPLATE_USER — so it errs toward leaving
    sections alone.

    Order is preserved deliberately: the funder expects the template's own
    sequence, so nothing here reorders. The keeper is the entry with the most
    `required` elements — the summary-table twin is always the thinner one — but
    it keeps the earlier slot in the list.
    """
    out: List[Dict[str, Any]] = []
    seen: Dict[frozenset, int] = {}
    for sec in sections:
        key = _section_key(sec.get("name"))
        if not key or key not in seen:
            if key:
                seen[key] = len(out)
            out.append(sec)
            continue
        keep = out[seen[key]]
        merged = list(keep.get("required") or [])
        for r in sec.get("required") or []:
            if r not in merged:
                merged.append(r)
        richer = sec if len(sec.get("required") or []) > len(keep.get("required") or []) else keep
        out[seen[key]] = {
            "id": richer.get("id") or keep.get("id"),
            "name": richer.get("name") or keep.get("name"),
            "required": merged,
            # A summary column caps at a few hundred words; the body chapter is
            # the one that may be uncapped. Never inherit the tighter cap.
            "word_limit": (None if keep.get("word_limit") is None or sec.get("word_limit") is None
                           else max(keep["word_limit"], sec["word_limit"])),
        }
    return out


def parse_template(llm: Any, template_text: str) -> Optional[Dict[str, Any]]:
    """Transcribe a proposal template into a `structure` block, or None if it
    yields nothing usable — in which case the pipeline plans a layout itself."""
    raw = llm.generate_text(
        system_prompt=TEMPLATE_SYSTEM,
        user_prompt=TEMPLATE_USER.replace("${template_text}", template_text[:MAX_GUIDELINE_CHARS]),
    )
    data = _parse_json(raw)
    sections = data.get("core_sections") if isinstance(data, dict) else None
    if not isinstance(sections, list) or not sections:
        return None
    clean = []
    for i, s in enumerate(sections, 1):
        if not isinstance(s, dict) or not s.get("name"):
            continue
        clean.append({
            "id": str(s.get("id") or f"sec_{i}").strip(),
            "name": str(s["name"]).strip(),
            "required": [str(r).strip() for r in (s.get("required") or []) if str(r).strip()],
            "word_limit": s.get("word_limit") if isinstance(s.get("word_limit"), int) else None,
        })
    if not clean:
        return None
    merged = _dedupe_sections(clean)
    if len(merged) < len(clean):
        print(f"  [template] 合并重复章节 {len(clean)} -> {len(merged)}")
    return {
        "template": str(data.get("template") or "申请书模板").strip(),
        "core_sections": merged,
        "rules": [str(r).strip() for r in (data.get("rules") or []) if str(r).strip()],
    }


def parse_topic_doc(llm: Any, topic_text: str) -> Dict[str, Any]:
    """Transcribe a topic the applicant already settled on."""
    raw = llm.generate_text(
        system_prompt=TOPIC_DOC_SYSTEM,
        user_prompt=TOPIC_DOC_USER.replace("${topic_text}", topic_text[:MAX_GUIDELINE_CHARS]),
    )
    data = _parse_json(raw)
    title = str(data.get("title") or "").strip()
    if not title:
        raise ValueError("选题文档解析失败：未能识别出课题名称")
    return {
        "title": title,
        "domain": str(data.get("domain") or "general").strip(),
        "background": str(data.get("background") or "").strip(),
        "challenges": [str(c).strip() for c in (data.get("challenges") or []) if str(c).strip()],
        "fit": "",
        "source": "uploaded",
    }


# English filename markers are matched as whole tokens, not substrings: "form"
# is inside "reform" and "information", "call" is inside "recall".
_NON_WORD = re.compile(r"[^a-z0-9一-鿿]+")

_EN_TEMPLATE_WORDS = {"template", "form", "sample", "proforma", "blank", "worksheet"}
_EN_TOPIC_LIST_WORDS = {"topics", "topiclist"}
_EN_GUIDELINE_WORDS = {"call", "guidance", "guidelines", "guide", "scheme",
                       "funding", "award", "awards", "grant", "opportunity"}
_EN_TOPIC_WORDS = {"topic", "idea", "concept", "abstract"}


def guess_role(filename: str) -> str:
    """Filename-only guess, used as the fallback when classification fails.

    Chinese markers are matched as substrings (no word boundaries to rely on);
    English ones as whole tokens. A Wellcome application form arrives as
    `sample-full-app-form-wellcome-discovery-award.pdf`, which matched none of
    the original template words and fell through to the `guideline` default.
    """
    raw = (filename or "").lower()
    toks = set(_NON_WORD.sub(" ", raw).split())

    def hit(cn, en):
        return any(w in raw for w in cn) or bool(toks & en)

    if hit(("模板", "提纲", "格式", "样表", "样张", "表格", "申请表", "投标书"),
           _EN_TEMPLATE_WORDS):
        return "template"
    if hit(("选题", "课题目录", "topic list"), _EN_TOPIC_LIST_WORDS):
        return "topic_list"
    # Checked before "课题": a file called 课题申报指南 is a call, not a topic.
    if hit(("指南", "通知", "公告", "申报要求", "招标"), _EN_GUIDELINE_WORDS):
        return "guideline"
    if hit(("课题", "构思", "方案书"), _EN_TOPIC_WORDS):
        return "topic"
    return "guideline"


# Wording that only a call document uses. A single topic description states what
# will be studied; it does not set a budget, a duration or deliverable counts.
# The English half exists because the Chinese markers scored 0 on both Wellcome
# PDFs, leaving the classifier with no deterministic backstop at all.
_CALL_MARKERS = (
    "经费", "资助", "万元", "预算", "申报", "申请人", "申报单位", "资格",
    "课题周期", "研究周期", "执行期", "不少于", "不超过", "遴选", "指南",
    "方向之一", "评审", "立项", "结题",
    "funding", "award", "grant", "eligib", "applicant", "deadline",
    "budget", "£", "apply", "scheme", "peer review", "must not exceed",
    "who can apply", "how to apply",
)


def _call_score(text: str) -> int:
    """How call-like a document reads. Used only to break a tie the classifier
    got wrong — a short call that describes one funding direction is easily
    mistaken for a topic description."""
    head = text[:4000].lower()
    return sum(1 for w in _CALL_MARKERS if w in head)


TOPIC_LIST_SYSTEM = """你是文档转写助手。用户提供了一份招标选题清单，你要把其中每一条选题原样抽取出来。

**逐条照抄，不要改写、归并、概括或补充**。清单里有多少条就抽多少条。只输出 JSON。"""

TOPIC_LIST_USER = """抽取下面清单中的全部选题。

## 要求
- 每条给出序号（原文的编号，没有就按出现顺序编）与选题名称原文。
- 部分选题名称末尾带 `*` 或类似标记（通常表示"方向性选题"，可自拟具体题目），保留该标记。
- 页眉页脚、栏目标题（如"一、马克思主义"）不是选题；栏目标题放进 `category` 字段，
  归属于其后各条选题。
- **不要遗漏**：宁可多抽，不要漏抽。

## 清单原文
${list_text}

只输出：{"topics": [{"no": "1", "title": "选题名称原文", "category": "所属栏目，无则空字符串"}]}"""


def parse_topic_list(llm: Any, list_text: str, chunk_chars: int = 6000) -> List[Dict[str, str]]:
    """Extract every entry from a catalogue of candidate topics.

    Chunked because these run to hundreds of entries: asking for all of them in
    one response invites the model to summarise or stop early, and a truncated
    catalogue silently removes options the applicant is required to choose from.
    """
    text = list_text.strip()
    chunks = [text[i:i + chunk_chars] for i in range(0, len(text), chunk_chars)] or [""]
    out: List[Dict[str, str]] = []
    seen = set()
    for chunk in chunks:
        try:
            raw = llm.generate_text(
                system_prompt=TOPIC_LIST_SYSTEM,
                user_prompt=TOPIC_LIST_USER.replace("${list_text}", chunk))
            items = (_parse_json(raw) or {}).get("topics") or []
        except Exception:
            continue
        for it in items:
            if not isinstance(it, dict):
                continue
            title = str(it.get("title") or "").strip()
            key = re.sub(r"\s+", "", title)
            if len(title) < 6 or key in seen:
                continue
            seen.add(key)
            out.append({"no": str(it.get("no") or len(out) + 1).strip(),
                        "title": title,
                        "category": str(it.get("category") or "").strip()})
    return out


def topics_as_directions(topics: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Present a topic catalogue as selectable directions.

    For calls that mandate bidding on a listed topic — "自选课题不予受理" — the
    catalogue *is* the set of directions, so it belongs in the same picker rather
    than a parallel one.
    """
    return [{"id": f"t{i}", "name": t["title"], "detail": t.get("category", "")}
            for i, t in enumerate(topics, 1)]


CLASSIFY_SYSTEM = """你是文档分类助手。给定若干份文档的文件名与开头片段，判断每份属于哪一类。只输出 JSON。"""

CLASSIFY_USER = """判断每份文档的类别：

- guideline：**资助指南 / 课题申报通知 / Call for Proposals**。特征是规定资助计划、经费、周期、
  申报资格、须交付的成果、可选研究方向。
- template：**申请书模板或提纲**。特征是列出申请书应包含哪些章节、每章写什么、字数限制，
  本身不含具体研究内容。
- topic_list：**招标选题清单 / 选题指南 / 课题目录**。特征是**成批罗列大量并列的课题名称**
  （常带序号，几十到数百条），供申请人从中挑选，而不是描述某一个课题。
- topic：**单个已确定的选题说明**。特征是围绕**一个**具体课题展开，说明它要做什么、为什么做。

  topic 与 topic_list 的区别只看数量：罗列多条备选 → topic_list；只讲一个 → topic。

## 判定优先级（按顺序判断，命中即定）

1. 文档主体是**待填写的表格、栏目名、填写说明、字数限制**，没有实质研究内容
   → **template**。文档里出现"经费预算""申报资格""成果形式"等**栏目名**不改变这一判定：
   模板会列出这些栏目，但不会规定具体数额与条件。
2. 文档**规定**了经费额度、课题周期、申报资格、须交付的成果数量、评审或立项流程中的任意一项
   （给出了具体数值或条件，而不只是留出填写位置）→ **guideline**。
   哪怕它篇幅很短、只描述一个研究方向、读起来像在介绍某个课题，也仍是 guideline ——
   规定"谁能申报、给多少钱、要交什么"的只可能是指南。
3. 文档成批罗列并列的课题名称 → **topic_list**。
4. 以上都不是，文档只讲一个具体课题要做什么、为什么做，且**不涉及经费与申报规定**
   → **topic**。

同一批文档中可以有多份同类，也可以缺某一类，不要为了凑齐四类而强行分配。

## 文档
${docs}

只输出：{"roles": {"文件名": "guideline|template|topic_list|topic"}}"""


def classify_documents(llm: Any, docs: List[Dict[str, str]]) -> Dict[str, str]:
    """Decide what each uploaded document is, so the user need not label them.

    `docs` is [{"filename": ..., "text": ...}]. Falls back to the filename
    heuristic for anything the model does not classify.
    """
    result = {d["filename"]: guess_role(d["filename"]) for d in docs}
    # 1500 rather than a few hundred characters: a short call fits entirely, and
    # its budget and deliverable clauses — the things that identify it — usually
    # sit after the opening paragraph.
    listing = "\n\n".join(
        f"【{d['filename']}】\n{d['text'][:1500]}" for d in docs)
    try:
        raw = llm.generate_text(system_prompt=CLASSIFY_SYSTEM,
                                user_prompt=CLASSIFY_USER.replace("${docs}", listing))
        roles = (_parse_json(raw) or {}).get("roles") or {}
        for name, role in roles.items():
            if name in result and role in ("guideline", "template", "topic_list", "topic"):
                result[name] = role
    except Exception:
        pass

    # Deterministic backstop. The classifier is unstable on short calls that
    # describe a single funding direction: it labels them `topic`, the call is
    # then never parsed, and the user is handed an empty form. If nothing was
    # called a guideline, promote whichever document reads most like one.
    if "guideline" not in result.values():
        cands = [d for d in docs if result[d["filename"]] == "topic"]
        best = max(cands, key=lambda d: _call_score(d["text"]), default=None)
        if best is not None and _call_score(best["text"]) >= 4:
            result[best["filename"]] = "guideline"
    return result


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
                   n: int = 3, guidance: str = "") -> List[Dict[str, Any]]:
    """Propose candidate topics under a chosen direction. This is the one stage
    that invents rather than transcribes.

    `guidance` is the applicant's own steer — a rough idea, a technique they want
    used, a sub-area to avoid. It is injected as a hard requirement rather than a
    hint, since a user who bothers to type it means it.
    """
    steer = ""
    if guidance.strip():
        steer = ("\n\n## 申请人的要求（必须满足）\n" + guidance.strip() + "\n"
                 "以上是申请人对选题的明确要求，每个候选课题都必须符合；与其冲突的想法一律不要提出。")
    user = (TOPIC_USER
            .replace("${program}", call.get("program", ""))
            .replace("${direction_name}", direction.get("name", ""))
            .replace("${direction_detail}", direction.get("detail", ""))
            .replace("${requirements}", "\n".join(f"- {r}" for r in call.get("requirements", [])) or "（未列明）")
            .replace("${constraints}", "\n".join(f"- {c}" for c in call.get("constraints", [])) or "（未列明）")
            .replace("${n}", str(n))) + steer
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
               topic: Dict[str, Any], task_id: str = "task_web",
               structure: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Assemble the task dict `run_pipeline` and `evaluation` consume.

    Section layout comes from the first of: an uploaded template (`structure`),
    a layout the call itself mandates, or nothing — and nothing is a real answer,
    leaving the pipeline's STRUCTURE_PLANNER to design one for this call rather
    than a hardcoded default being assumed.
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
    chosen = structure or call.get("structure")
    if isinstance(chosen, dict) and chosen.get("core_sections"):
        task["structure"] = chosen
        task["provenance"]["structure_from"] = "uploaded_template" if structure else "guideline"
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
