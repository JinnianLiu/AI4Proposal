"""Figure generation for proposal figures, in two stages.

The writer emits `[figure: <图注> || <图位描述>]` markers. Turning such a marker
into a usable figure is a *design* problem, not a formatting one, so it runs as
two separate model calls:

  1. plan_figure()   an LLM reads the slot description plus proposal context and
                     returns a JSON plan: composition type, the argument the
                     figure must make, the three optional layers, a Chinese
                     title/caption for the document, and a long English prompt.
  2. render_figure() that English prompt goes to an images/generations endpoint
                     (gpt-image-2 behind an OpenAI-compatible proxy).

Splitting them matters because the image model cannot do layout reasoning from a
Chinese paragraph, and because a plan that fails validation can be dropped before
any image money is spent. A slot with fewer than two identifiable research
objects is skipped rather than drawn as a generic box diagram.

Text inside the picture is short English labels only: image models garble CJK
glyphs at small sizes, so all Chinese lives in the title and caption that the
pipeline writes into the document instead.

No keys are hardcoded — everything comes from arguments / environment.
"""
from __future__ import annotations

import base64
import json
import os
import re
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

# Landscape by default: a square canvas forces multi-zone figures to stack
# vertically and cramps them. Override with AI4PROPOSAL_IMAGE_SIZE.
DEFAULT_SIZE = "1536x1024"

# Slots whose subject is a schedule: a picture of a Gantt chart adds nothing a
# table does not say better, and image models fabricate dates onto it.
PROGRESS_PATTERNS = (
    r"甘特图?", r"项目进度", r"项目排期", r"项目时间线",
    r"年度计划", r"月度计划", r"阶段计划", r"任务排期", r"项目里程碑",
)

VALID_COMPOSITIONS = {"dashboard", "pipeline", "system_loop", "architecture"}

# Constraints the image model must see. The planner is told to include them; if
# it drops any we append them rather than discard an otherwise good plan.
REQUIRED_PROMPT_PHRASES = (
    "no Chinese text", "no long paragraphs", "no timeline", "no Gantt chart",
    "no project schedule", "no decorative background", "no photorealistic rendering",
    "no long diagonal arrows", "no fabricated data", "no numerical claims",
    "no performance percentages",
)

MIN_PROMPT_WORDS = 120


class ImageGenerationError(RuntimeError):
    """The image endpoint failed, or returned something we cannot save."""


# ─────────────────────────────── stage 1: plan ───────────────────────────────

PLANNER_SYSTEM = """你是一名科研项目申请书插图策划专家，服务于自然科学、工程技术、医学、生命科学、农业、材料、能源、环境、地球科学、管理科学、教育、社会科学、人文及交叉学科。

你的任务不是直接绘图，而是把申请书中的一个中文图位描述，转换为可由图像生成模型执行的英文科研插图提示词。

你不得预设项目属于某个学科，也不得预设项目存在算法、模型、系统、设备、实验、问卷、样本、传感器、芯片或软件。只根据项目名称、上下文与图位描述，识别真实出现或可直接推导的研究对象、过程、条件、机制与关系。

一、图件目标

每张图必须服务于一项明确科研论证，而不是把文字搬成方框流程图。可以展示：研究问题与关键变量；理论框架、作用机制或因果路径；实验设计、数据采集、样本分组或分析流程；技术路线、实施过程或治理过程；多尺度结构、层级关系、时空演变；比较关系、验证逻辑、评价框架、反馈优化；代表性案例、实施前后状态或验证过程。

一张图优先表达一个主张，不要用单张图替代整份申请书。

二、关系识别

必须提取至少两个明确科研对象及其关系（因果、影响、调节、中介、反馈；输入—处理—输出；结构—功能、条件—响应；对象—方法—结果；分层、并列、对比、协同、约束；时间阶段或空间尺度；采集—处理—分析—验证；问题—干预—评估—迭代）。

仅使用输入中明确存在或可直接推导的对象和关系。禁止凭空加入变量、机制、设备、材料、样本、干预措施、实验分组、指标、场景、结论或数据。若识别不出至少两个明确对象及其关系，选择 skip。

三、构图类型（选一个主构图）

1. dashboard —— 多维比较、案例对照、指标体系、评估框架、状态变化或定性趋势。
2. pipeline —— 实验步骤、工艺、研究流程、数据流程、分析流程、生命周期过程。
3. system_loop —— 反馈调控、动态演化、循环机制、自适应优化、监测—预警—响应闭环。
4. architecture —— 理论框架、概念模型、系统组成、多层结构、尺度嵌套、要素耦合。

四、论证层次（按图位支持程度取二至三层）

1. 总体关系区：研究对象、关键要素、主要环节、层次结构或主链路。
2. 关键机制/方法细节区：以局部放大、剖面、流程展开、变量作用路径解释一个关键过程如何发生，须呈现 输入或初始条件 → 核心变化或决策 → 输出或响应。
3. 代表性案例/验证区：抽象案例、实验场景、组间对比、实施前后对比或验证闭环，须呈现 对象或初始状态 → 处理、观察或干预 → 结果或终态。

图位不支持三层时只保留有依据的层次，不得为形式完整而虚构。

五、学科自适应视觉元素

按项目内容选择，而不是套用固定的计算机图标；且必须与原始描述对应：
- 生命医学：细胞、组织剖面、器官轮廓、分子相互作用、样本管、临床路径、队列分组；
- 化学与材料：分子结构、晶格、微观结构剖面、反应路径、制备装置、性能曲线；
- 环境与地球科学：区域地图、流域、土层剖面、气候要素、污染迁移、遥感栅格、时空分布；
- 农业与生态：作物生长阶段、土壤层、生态网络、物种关系、管理措施、环境响应；
- 能源与工程：装置剖面、能量流、工艺单元、传热传质路径、控制回路、运行状态；
- 管理与社会科学：主体关系网络、政策措施、资源流、行为路径、访谈或问卷分析流程；
- 教育研究：教学活动、学习过程、能力发展阶段、干预设计、评价反馈；
- 人文研究：文本材料、历史脉络、概念关系、证据链、空间文化分布、比较框架；
- 计算与信息：数据流、模型结构、模块关系、训练/验证流程、系统架构。

不得因为某领域常见就擅自加入上述元素。

六、视觉规范

- 白底、横向 16:9、干净的矢量信息图风格，出版级质量；
- 2 至 5 个承担不同论证职责的主视觉区域，核心关系最醒目；
- 不得把所有区域做成重复的竖向卡片或普通文字框；
- 配色简洁统一，且必须服务于真实对象、过程或状态；
- 用短的水平、垂直或正交箭头表达流程、因果、控制与反馈，允许局部弧形箭头表达循环；禁止跨越全图的长斜线箭头；
- 至少两类与研究内容匹配的科研视觉元素；
- 图中只能有少量、极短的英文标签；不要求生成中文文字；
- 不生成长段落、复杂公式、精确数值或密集小字；
- 不虚构数据集、实验结果、显著性、性能百分比、排名、样本量；
- 禁止时间线、甘特图、进度图、里程碑图；禁止装饰性背景、海报风格、卡通人物、照片写实渲染。

七、输出约束

仅输出调用方指定结构的合法 JSON，不输出解释、Markdown 或代码块。"""

PLANNER_USER = """请为以下申请书图位生成正式科研插图规划。

项目名称：
{title}

项目上下文：
{context}

图号：
{figure_id}

图位描述：
{description}

正文语言（title / subtitle / caption 必须用该语言）：
{doc_language}

仅输出下列合法 JSON：

{{
  "action": "draw | skip",
  "composition": "dashboard | pipeline | system_loop | architecture",
  "title": "用于正文的图题，语言同正文",
  "subtitle": "不超过50字的副标题，语言同正文",
  "main_message": "该图需要论证的核心科研关系",
  "overall_design": "中文说明：总体关系区展示哪些真实对象、层次、过程和关系",
  "mechanism_or_method_case": "中文说明：关键机制或方法如何以输入—过程—输出展示；不适用则填空字符串",
  "case_or_validation": "中文说明：代表性案例、实验设计或验证逻辑如何展示；不适用则填空字符串",
  "image_prompt_en": "供图像模型使用的详细英文提示词，220至650个英文单词",
  "caption": "图注，语言同正文",
  "reason": "仅 action=skip 时填写"
}}

作答规则：

1. 先判断图位是否包含至少两个明确科研对象及其关系。不包含则返回：
{{"action": "skip", "reason": "图位描述缺少至少两个可视化的明确科研对象及其关系"}}

2. action=draw 时：只使用项目名称、上下文与图位描述中明确出现或可直接推导的对象、条件、变量、过程与关系；不假定学科；不按常识补充未出现的样本、设备、算法、材料、指标、场景或结论；图必须体现一个明确论点，不得只是把文字改写成方框列表；在四种构图中选最匹配的一种；全图 2 至 5 个承担不同职责的主视觉区域；原描述不支持机制或案例时，对应字段填空字符串，不要虚构。

3. image_prompt_en 必须以这三句开头：
Create a publication-quality academic research figure.
White background. Horizontal 16:9 layout. Clean vector infographic style.

4. image_prompt_en 必须写明：总体构图与各视觉区域的位置；项目中真实存在的对象与关系；与主题匹配的视觉元素；一致的颜色编码及其含义；用短的水平、垂直或正交箭头表达过程、因果、层级与反馈；有机制区时写明 input/initial condition → internal process → output/response；有案例区时写明 subject/initial state → procedure or intervention → result/final state；图中仅保留少量极短英文标签。

5. image_prompt_en 必须包含以下英文限制语：
no Chinese text; no long paragraphs; no timeline; no Gantt chart; no project schedule; no decorative background; no photorealistic rendering; no long diagonal arrows; no fabricated data; no numerical claims; no performance percentages.

6. 不得要求图片生成：中文文字、长标题、长段文字、复杂公式；真实或虚构的精确数字、样本量、统计结果、显著性标记、性能百分比、排名；未由输入支持的数据集、机构、人物、品牌或应用场景；项目进度、年度计划、甘特图或里程碑。

7. title、subtitle、caption 用上面给出的**正文语言**书写，可直接写入正式申请书；overall_design 等
   规划说明字段仍用中文（它们不进正文，只供人工复核）。无论正文是哪种语言，image_prompt_en 始终是
   英文，且图内标签只能是极短英文——图像模型画不好小号中日韩字形。"""


def _one_line(value: Any, limit: int = 80) -> str:
    """Collapse to a single line — for titles, ids and error text."""
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _block(value: Any, limit: int = 12000) -> str:
    """Keep line breaks — for the English prompt and the slot description."""
    text = re.sub(r"[ \t]+", " ", str(value or "").strip())
    return re.sub(r"\n{3,}", "\n\n", text)[:limit]


def _safe_json(raw: str) -> Dict[str, Any]:
    try:
        start, end = raw.find("{"), raw.rfind("}") + 1
        if start < 0 or end <= start:
            return {}
        parsed = json.loads(raw[start:end])
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _skip(title: str = "", reason: str = "") -> Dict[str, Any]:
    return {
        "action": "skip", "composition": "", "title": title, "subtitle": "",
        "main_message": "", "overall_design": "", "mechanism_or_method_case": "",
        "case_or_validation": "", "image_prompt_en": "", "caption": "",
        "reason": reason or "图位描述不足以形成具体科研论证图。",
    }


def _ensure_required_phrases(prompt: str) -> str:
    """Append any mandatory negative constraint the planner left out. The body of
    the prompt is never rewritten — only the safety tail is completed."""
    lowered = prompt.lower()
    missing = [p for p in REQUIRED_PROMPT_PHRASES if p.lower() not in lowered]
    if not missing:
        return prompt
    return f"{prompt.rstrip()}\n\nMandatory restrictions: {'; '.join(missing)}."


def _english_words(text: str) -> int:
    return len(re.findall(r"[A-Za-z]+(?:['-][A-Za-z]+)?", text))


DEFAULT_TITLES = {"zh": "研究内容示意图", "en": "Overview of the proposed work"}

# Character caps on the fields that reach the document. They are runaway guards,
# not style rules — but a cap sized for Chinese cuts English mid-word: 80
# characters held a whole 图题 and left task_003 with "…framework for resolving
# revers". Non-CJK languages get roughly the 2.5x they need for the same content.
_FIELD_CAPS = {"zh": {"title": 80, "subtitle": 120, "caption": 180},
               "en": {"title": 200, "subtitle": 300, "caption": 450}}


def plan_figure(llm, figure_id: str, proposal_title: str, description: str,
                context: str = "", language: str = "zh") -> Dict[str, Any]:
    """Turn one figure slot into a drawing plan.

    `context` should carry the abstract / objectives / approach text: without it
    the planner invents research objects to fill the canvas. `language` is the
    document's language: the title and caption go straight into the proposal, so
    they follow it, while `image_prompt_en` stays English whatever it is.
    Returns a dict whose `action` is "draw" or "skip"; callers never need to
    handle exceptions.
    """
    lang = language if language in DEFAULT_TITLES else "zh"
    if any(re.search(p, f"{proposal_title} {description}") for p in PROGRESS_PATTERNS):
        return _skip("进度类图件", "项目进度、时间线或里程碑类图件不生成。")
    if llm is None:
        return _skip("", "缺少规划模型，无法将图位描述转为生图提示词。")

    try:
        raw = llm.generate_text(
            system_prompt=PLANNER_SYSTEM,
            user_prompt=PLANNER_USER.format(
                title=_one_line(proposal_title, 200),
                context=_block(context, 6000),
                figure_id=_one_line(figure_id, 30),
                description=_block(description, 1500),
                doc_language={"zh": "简体中文", "en": "英文（English）"}[lang],
            ),
        )
    except Exception as exc:
        return _skip("", f"图件规划调用失败：{_one_line(exc, 180)}")

    plan = _safe_json(raw)
    if _one_line(plan.get("action"), 20).lower() == "skip":
        return _skip(_one_line(plan.get("title")), _one_line(plan.get("reason"), 180))

    prompt = _block(plan.get("image_prompt_en"))
    if len(prompt) < 30:
        return _skip(_one_line(plan.get("title")), "规划未产出有效的 image_prompt_en。")
    prompt = _ensure_required_phrases(prompt)
    words = _english_words(prompt)
    if words < MIN_PROMPT_WORDS:
        # A short prompt means the planner described the figure in the abstract;
        # the image model would fall back to a generic box diagram.
        return _skip(_one_line(plan.get("title")), f"image_prompt_en 过短（{words} 词）。")

    composition = _one_line(plan.get("composition"), 30).lower()
    caps = _FIELD_CAPS[lang]
    plan.update({
        "action": "draw",
        "composition": composition if composition in VALID_COMPOSITIONS else "architecture",
        "title": _one_line(plan.get("title"), caps["title"]) or DEFAULT_TITLES[lang],
        "subtitle": _one_line(plan.get("subtitle"), caps["subtitle"]),
        "main_message": _one_line(plan.get("main_message"), 300),
        "overall_design": _one_line(plan.get("overall_design"), 1200),
        "mechanism_or_method_case": _one_line(plan.get("mechanism_or_method_case"), 1200),
        "case_or_validation": _one_line(plan.get("case_or_validation"), 1200),
        "image_prompt_en": prompt,
        "caption": _one_line(plan.get("caption"), caps["caption"]),
        "reason": "",
    })
    return plan


# ────────────────────────────── stage 2: render ──────────────────────────────

# Some corporate/proxy chains break cert validation; mirror evidence.py's context.
_SSL_VERIFIED = ssl.create_default_context()

_SSL_UNVERIFIED = ssl.create_default_context()
_SSL_UNVERIFIED.check_hostname = False
_SSL_UNVERIFIED.verify_mode = ssl.CERT_NONE

_warned_insecure = False


def _urlopen(req: urllib.request.Request, timeout: float):
    """Open `req` with certificate verification, dropping to an unverified
    connection only when the TLS chain itself fails (proxies that terminate TLS
    with their own CA). Mirrors evidence._urlopen. This path carries the image
    API key, so verification matters more here than for public retrieval.
    Set AI4PROPOSAL_INSECURE_TLS=1 to skip the verified attempt."""
    global _warned_insecure
    insecure = os.getenv("AI4PROPOSAL_INSECURE_TLS", "").strip().lower() in {"1", "true", "yes", "on"}
    if not insecure:
        try:
            return urllib.request.urlopen(req, timeout=timeout, context=_SSL_VERIFIED)
        except ssl.SSLError:
            if not _warned_insecure:
                print("    [image] TLS 证书校验失败（可能是代理拆包），改用不校验连接")
                _warned_insecure = True
    return urllib.request.urlopen(req, timeout=timeout, context=_SSL_UNVERIFIED)


def image_config_from_env() -> Dict[str, str]:
    """Read image-gen settings from env (keeps keys out of source)."""
    return {
        "api_key": os.environ.get("AI4PROPOSAL_IMAGE_API_KEY", "").strip(),
        "base_url": os.environ.get("AI4PROPOSAL_IMAGE_BASE_URL", "https://api.chatanywhere.tech/v1").strip(),
        "model": os.environ.get("AI4PROPOSAL_IMAGE_MODEL", "gpt-image-2").strip(),
        "size": os.environ.get("AI4PROPOSAL_IMAGE_SIZE", DEFAULT_SIZE).strip() or DEFAULT_SIZE,
    }


def _endpoint(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/images/generations"):
        return base
    if base.endswith("/v1"):
        return base + "/images/generations"
    return base + "/v1/images/generations"


def _save_response(data: Dict[str, Any], out_path: Path) -> str:
    try:
        item = data["data"][0]
    except (KeyError, IndexError, TypeError) as exc:
        raise ImageGenerationError(
            f"响应中不存在 data[0]：{json.dumps(data, ensure_ascii=False)[:1000]}") from exc
    if not isinstance(item, dict):
        raise ImageGenerationError("响应中的 data[0] 不是对象。")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if item.get("b64_json"):
        out_path.write_bytes(base64.b64decode(item["b64_json"]))
        return "b64_json"
    if item.get("url"):
        req = urllib.request.Request(item["url"], headers={"User-Agent": "AI4Proposal/1.0"})
        with _urlopen(req, 240.0) as resp:
            out_path.write_bytes(resp.read())
        return "url"
    raise ImageGenerationError(
        f"响应既无 b64_json 也无 url：{json.dumps(data, ensure_ascii=False)[:1000]}")


def render_figure(prompt: str, out_path: Path, cfg: Optional[Dict[str, str]] = None,
                  timeout: float = 240.0, max_retries: int = 2) -> Optional[Path]:
    """Send an already-planned English prompt to the images endpoint and save the
    PNG. Returns the path, or None on failure — the caller degrades to a text
    placeholder rather than aborting a proposal that is otherwise complete."""
    cfg = cfg or image_config_from_env()
    if not prompt.strip() or not cfg.get("api_key"):
        return None

    url = _endpoint(cfg["base_url"])
    body = json.dumps({
        "model": cfg["model"], "prompt": prompt, "n": 1,
        "size": cfg.get("size") or DEFAULT_SIZE,
    }).encode()
    headers = {"Authorization": f"Bearer {cfg['api_key']}", "Content-Type": "application/json"}

    data = None
    for attempt in range(max_retries + 1):
        try:
            req = urllib.request.Request(url, data=body, headers=headers)
            with _urlopen(req, timeout) as resp:
                data = json.loads(resp.read().decode())
            break
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="ignore")[:300]
            print(f"    [image] HTTP {e.code}: {detail}")
            if e.code in (429, 500, 502, 503) and attempt < max_retries:
                time.sleep(2 ** attempt * 3)
                continue
            return None
        except Exception as e:
            print(f"    [image] {type(e).__name__}: {str(e)[:200]}")
            if attempt < max_retries:
                time.sleep(2 ** attempt * 2)
                continue
            return None
    if not data:
        return None

    try:
        _save_response(data, out_path)
    except Exception as e:
        print(f"    [image] 保存失败：{str(e)[:200]}")
        return None
    if not out_path.exists() or out_path.stat().st_size == 0:
        return None
    return out_path


def write_plan_record(image_path: Path, plan: Dict[str, Any]) -> Path:
    """Save the plan next to its PNG, so a figure can be judged, re-rendered or
    hand-edited later without re-running the pipeline."""
    record = image_path.with_suffix(".plan.json")
    record.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    return record
