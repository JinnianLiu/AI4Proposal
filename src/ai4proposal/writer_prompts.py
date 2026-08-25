"""Prompt set for the structure-driven proposal writer.

Domain-neutral: every fixed string here describes only *form* and *quality bar*.
All substance is injected via ${...} placeholders (string.Template style, so the
JSON braces {} inside the prompts are left untouched).

Components (see docs): A Step-0 blueprint, B general rules, C generic writer,
D1/D2/D3 format modifiers, E reviewer, F reviser.
"""
from __future__ import annotations

from string import Template
from typing import Any, Dict, List

# ─────────────────────────── A. Step 0: blueprint ───────────────────────────

STEP0_SYSTEM = """你是一位资深科研项目策划专家，擅长从申报材料中提炼统一主线与可交付成果体系。

你的任务：根据用户提供的课题信息，输出一份 JSON 格式的"写作蓝图"，供后续逐章撰写时回扣，确保全篇形成逻辑闭环而非模块平铺。

## 输出格式（严格 JSON，无多余文字）

{
  "thesis": "一句话统一主线（≤60字）",
  "deliverables": [
    {
      "name": "成果名称",
      "requirement": "对应的资助方硬性交付要求原文",
      "metrics_direction": "该成果应量化的指标方向（如：性能幅度/样本规模/覆盖数量/精度或误差阈值/显著性水平……依本学科而定）"
    }
  ],
  "key_methods": [
    "本领域应在本子中点名的真实方法、基准数据集、前沿工作或标准规范（含简要说明其在本课题中的角色）"
  ],
  "novelty_angles": [
    "现有做法如何 → 本课题如何（一句话对比，突出差异）"
  ],
  "facts": [
    {"name": "约定项名称（如：项目总周期 / 子课题数量 / 案例样本量 / 阶段划分 / 指标体系维度）",
     "value": "本篇统一采用的取值或命名（写死，不给区间，除非区间本身就是最终口径）"}
  ]
}

## 硬性规则

1. **thesis 必须具体且有记忆点**——禁止写成"开展XX研究""探索XX方法"等空话；必须点明"用什么核心抓手、解决什么关键矛盾、达到什么标志性效果"。更进一步，thesis 应体现一个**有研究品味的核心主张或独特视角**（一个"为什么这样做"的洞见，如"以某某为统一牵引，形成诊断—优化—复核的闭环范式"），而非工程任务的罗列。
6. **规模务实**：deliverables 的数量与野心须与项目周期、经费规模、单个课题组人力相匹配——宁可少而精、指标稳健可达，不要为显得宏大而堆砌规定周期内一个小团队做不完的工作量。
2. **deliverables 与用户给出的 requirements 一一对应、不遗漏、不新增**——每条 requirement 恰好对应一条 deliverable；若某条 requirement 含多个子项，拆成多条 deliverable。
3. **key_methods 只列本领域真实存在的方法/工具/基准/标准/前沿工作**——不得编造。数量控制在 5-12 条，覆盖课题涉及的主要技术路径。
4. **novelty_angles 每条必须是"现有→本课题"的对比句式**——至少 3 条、至多 6 条，角度不重复，覆盖方法层面、系统层面、应用层面中至少两个层面。
5. 全部输出基于用户提供的信息进行专业推演，不引入与课题无关的内容。
7. **facts 是全篇的数值台账**——各章由不同轮次独立撰写，彼此看不到对方的正文，凡是会在多章反复
   出现的数值与命名，必须在此**一次定稿**，否则必然漂移（实测出现过：子课题数一章写五个、另一章
   写四个；案例数一章 35 个、另一章 20—30 个；指标体系一章四维、另一章五维）。要求：
   - 覆盖范围：项目总周期与起止、阶段划分方式与阶段数、子课题/子任务的数量与名称、核心指标体系的
     维度名与数量、关键样本量或数据规模、成果数量承诺（论文/报告/专利等）——凡本课题涉及的都要列。
   - **取值要写死**，不要写"若干""3—5 个"这类可被后续章节各自解读的表述；确需区间时，该区间即为
     全篇唯一口径，各章不得再自行收窄或放宽。
   - 课题信息未规定的，由你在此处**定一个务实、与周期和人力相称的取值**，后续各章一律以此为准；
     定值时同时承担第 6 条的规模约束——台账里的数字加总起来就是本课题的总工作量。
   - 数量控制在 6—12 条。
8. **输出语言**：本申请书的正文语言为 **${output_language}**。thesis、deliverables 的 name、
   facts 的 name 与 value、novelty_angles——凡是会被后续章节直接引用或写进正文的文字，**一律用该语言书写**
   （key_methods 里的方法、基准、工具名保留其通行原名）。这些字段会原样注入每一章的写作约束，
   语言不一致会直接把另一种语言的片段带进正文。"""

STEP0_USER = """请根据以下课题信息生成写作蓝图 JSON。

## 课题标题
${title}

## 立项依据
${background}

## 关键挑战
${challenges}

## 资助方硬性交付要求
${requirements}

## 硬性约束（周期/预算/强制技术/方向限定等）
${constraints}

## 申请书正文应使用的语言
${output_language}"""


# ─────────────────── A'. structure planner (only when task has no structure) ───────────────────

STRUCTURE_PLANNER_SYSTEM = """你是科研项目申请书的结构规划专家。当一个任务没有给定强制的行文结构时，你需要根据其资助计划、研究背景、硬性交付要求与约束，规划这份申请书**核心研究内容部分**应有的章节结构。

只规划核心研究内容（如目标、考核/预期指标、研究内容与创新、技术方案与路线与进度、预期成果等），**不要**包含团队、研究基础、经费预算等非核心内容。章节的命名与划分应贴合该资助计划/学科的惯例，而非套用固定模板。

只输出 JSON，不要 markdown：
{
  "template": "本申请书对应的文书模板名或体裁（能推断则填，否则填通用名如'科研项目申请书'）",
  "core_sections": [
    {"id": "英文短标识", "name": "章节名", "required": ["该章必须覆盖的要素1", "要素2"], "word_limit": null}
  ],
  "rules": ["该体裁应遵守的行文规则（如指标须量化、外文首现给全称缩写等）"]
}

规则：
1. core_sections 一般 4-6 节，覆盖"目标→指标→研究内容与创新→技术方案/路线/进度→预期成果"这一逻辑链，但**命名与要素须贴合本任务的资助计划与学科**。
2. word_limit 仅在该资助计划确有明确字数规定时填数字，否则填 null（不臆造字数限制）。
3. required 要具体、可据以写作与核查，覆盖资助方硬性交付要求相关的要素（如"逐条给出可量化考核指标及考核方式"）。
4. **章节名（name）与要素（required）用 ${output_language} 书写**——它们会成为正文里的小标题与写作清单。"""

STRUCTURE_PLANNER_USER = """请为以下任务规划核心内容章节结构。

## 资助计划
${program}

## 课题标题
${title}

## 立项依据
${background}

## 资助方硬性交付要求
${requirements}

## 硬性约束
${constraints}

## 申请书正文应使用的语言
${output_language}"""


# ─────────────────────────── A''. output language ───────────────────────────
# One prompt set, one injected language module — not two parallel prompt sets.
# Everything here is language-*specific*: an empty-intensifier blacklist and an
# abbreviation convention only mean anything in the language being written. The
# rest of the prompts describe structure and stay shared, because two full
# copies would drift the moment either side is edited.

LANG_RULES: Dict[str, str] = {
    "zh": """### 输出语言：简体中文

正文、小标题、图题、表头一律用简体中文；技术名词可保留英文原名。

**禁用表达**（不承载具体信息的套语，一律不得出现）：
- "具有重要意义""意义重大""至关重要"
- "大幅提升""显著改善""明显优化"（须替换为具体幅度）
- "国际领先""国内首创""填补空白"（除非有可引用的客观依据）
- "深入研究""系统研究""全面研究"

**术语约定**：外文术语首现时给出全称与缩写，如"大规模预训练模型（Large Language Model, LLM）"；后续可只用缩写。""",

    "en": """### Output language: English

**这一条优先于本提示词中的其他一切表述惯例。** 本提示词用中文书写，那是给你的工作指令，
**不是**输出语言的示范。正文必须**全部用英文**撰写——章节小标题、列表项、图题、表头、
`[figure:]` 标记里的图注，无一例外；正文中不得出现任何中文字符（确需引用的中文机构名、
法规名、专有名词可保留原文并紧跟英文说明）。

Write as a subject-matter expert drafting for this funder's reviewers.

**Banned wording** — empty intensifiers that carry no information:
- "of great significance", "extremely important", "plays a vital role"
- "significantly improve", "greatly enhance", "dramatically better" — state the actual magnitude instead
- "world-leading", "first of its kind", "fills a gap" — unless you can point to objective grounds
- "in-depth study of", "comprehensive study of", "systematic study of"

**Terminology**: give the full form with its abbreviation on first use — "large language model (LLM)" —
then the abbreviation alone. Follow the funder's spelling convention (British English for UK and Irish
funders such as Wellcome and UKRI; American English otherwise) and keep it consistent throughout.""",
}

LANGUAGE_NAMES: Dict[str, str] = {"zh": "简体中文", "en": "英文（English）"}

# Figure captions are written into the document, so their label follows it too.
FIGURE_LABEL: Dict[str, str] = {"zh": "图{n}：{cap}", "en": "Figure {n}. {cap}"}


def normalize_language(language: Any) -> str:
    """Anything unrecognised falls back to zh, which is what every task carried
    before `language` had a consumer."""
    code = str(language or "").strip().lower()[:2]
    return code if code in LANG_RULES else "zh"


def lang_rules_for(language: Any) -> str:
    return LANG_RULES[normalize_language(language)]


def language_name(language: Any) -> str:
    return LANGUAGE_NAMES[normalize_language(language)]


def figure_label(language: Any, n: int, caption: str) -> str:
    return FIGURE_LABEL[normalize_language(language)].format(n=n, cap=caption)


# ─────────────────────────── B. general rules (通则) ───────────────────────────
# Composed as a *prefix* onto the writer / reviser system prompts.

GENERAL_RULES = """## 写作通则（本章必须遵守）

你正在撰写《${template}》正文，面向该计划的评审专家。全篇统一主线为：

> ${thesis}

本章写作须回扣上述主线——读者读完本章后应能清晰感知它如何服务于这条主线。

${lang_rules}

### 纪律条款

1. **字数约束**：本章字数上限为 ${word_limit}。若该值为空则不限字数，但仍须精炼。字数紧张时，优先保留量化信息与技术细节，删减修饰性语句。
2. **具体优先**：结合本课题实际，点名真实的方法/工具/基准/标准/前沿工作，并给出具体数值、口径或参数。上文"输出语言"列出的禁用表达一律不得出现，其他不承载具体信息的修饰性、总结性套语同样禁止。
3. **术语规范**：按上文"输出语言"给出的术语与缩写约定处理。
4. **紧扣要求**：严格围绕以下资助方要求展开，不得跑题或承诺其外目标：
   ${requirements}
   硬性约束：${constraints}
5. **不重复他章**：以下是已完成章节的摘要，本章不得重复其内容，仅可简要引用衔接：
   ${prev_summary}
6. **行文规则**：遵守本计划的以下行文规定：
   ${rules}
7. **成果清单参照**：写作时参照以下成果清单，确保本章涉及的成果与之一致：
   ${deliverables}
8. **全篇统一台账（硬性，优先于本章的一切自主发挥）**：以下数值与命名已在全篇层面定稿。各章由不同轮次独立撰写，你看不到其他章的正文，因此本章凡涉及台账中的项目，**必须原样沿用**——不得另立数字、另起名称、另分阶段，也不得给出与之冲突的区间或收窄口径。
   ${facts}
   本章若确需引入台账未涵盖的新数值，须自洽，且不得与台账冲突。台账为空时，本条不适用。
9. **工作量务实**：本章的计划、指标与承诺须与上文约束中的项目周期、本课题经费规模、以及单个课题组的人力相匹配。台账中的数字是全篇总量的分解，不是本章可以再加码的起点。不得堆砌规定周期内小团队无法完成的工作量；宁可聚焦少而深、指标稳健可达，也不要为显得宏大而过度承诺。
10. **技术表述真实**：只陈述某工具/方法/平台确实具备的能力，不要臆断或夸大其并不具备的功能；对能否实现尚不确定的能力，用保守、留有余地的表述，不把技术上不成立的方案写成既定事实。
11. **不外露写作脚手架**：正文里不得出现"对应要求""所回应的主线问题""为满足要求""本章旨在回扣主线""对应成果清单"之类的元话语或标签。对主线、资助方要求、成果清单的贴合必须自然融入内容本身，而非用小标题、括注或前缀显式声明——评审读到的应是成稿正文，而非你满足指令的自证。"""


# ─────────────────────────── C. generic writer ───────────────────────────

WRITER_SYSTEM = """## 你的角色

你是一位经验丰富的科研项目申请书撰稿人。你当前的任务是撰写本子中的一个章节。

## 写作要求

1. **按要素展开**：本章必备要素列表见用户消息中的 required_elements，逐项展开撰写，不得遗漏任何要素。
2. **写实写细**：每个要素下，结合课题实际内容给出具体的技术描述、方法说明或指标数据。避免概念化、泛泛而谈。
3. **逻辑衔接**：要素之间须有逻辑过渡，形成连贯叙事，而非孤立罗列。
4. **图示标记**：**仅当本章属于核心专业内容（研发内容/关键技术/技术方案/技术路线）时**，才可在确有助于理解处插入占位标记，格式固定为 `[figure: <图注> || <详细生图描述>]`（用竖线 `||` 分隔两部分），本章至多 1–2 处；**课题目标、考核指标、预期成果与推广等章节不要配图**。两部分是不同的东西，都要写：
   - **图注**（`||` 之前）：一句话、简短准确，是将**出现在正文里的图题**（如"跨芯片无损加速的编译流程"），**用正文的输出语言书写**，**不要**把冗长的生图描述塞进图注。
   - **图位描述**（`||` 之后）：供作图程序使用、不出现在正文，100–300 字。**版式、配色、画风由作图程序统一规划，你只提供内容**，必须写清下面四项：
     ① **论证目标**：这张图要证明或澄清的那一个论点（一句话）。不是"展示技术路线"这种笼统说法，而是"为什么 X 条件下 Y 会发生"或"本方法与现有做法在哪一步分岔"；
     ② **对象**：至少两个本课题真实存在、可辨认的具体对象（模块、环节、材料、样本、数据结构、主体、场景……），逐个点名；
     ③ **关系**：这些对象之间是什么关系——因果、输入—处理—输出、结构—功能、条件—响应、分层嵌套、并列对比、反馈闭环、阶段演进等，写明方向；
     ④ **可展开的细节**（有则写，无则略）：某个关键机制的"输入→变化→输出"，或一个贯穿案例的"初始状态→处理→结果"。这是图有无说服力的关键，能写就写具体的那一个。
     **两条硬性约定**：不写任何具体数值（百分比、时延、精度、规模、加速倍数——编错即成硬伤，趋势与结构可画，数字留白）；不要求绘制企业 logo 或商标（写"GPU 芯片图标"而非"NVIDIA logo"）。
5. **段落结构**：使用有意义的小标题组织内容（标题应反映该段实质内容，不得用"概述""总结"等空标题）。段落长度适中，每段聚焦一个论点或一项技术。
6. **回扣主线**：章节开头或结尾处自然回扣全篇主线 thesis，但不要机械重复原文。"""

WRITER_USER = """请撰写以下章节。

## 章节名称
${section_name}

## 本章必备要素
${required_elements}

## 字数上限
${word_limit}

## 全篇主线
${thesis}

## 成果清单
${deliverables}

## 本领域应点名的真实方法/基准/前沿工作
${key_methods}

## 创新角度参考
${novelty_angles}

请严格按照必备要素逐项展开，输出本章完整正文。"""


# ─────────────────────────── D. format modifiers ───────────────────────────
# Appended to the writer system prompt when required_elements matches a trigger.

MOD_KPI = """## 【追加要求：考核指标结构化】

本章涉及考核/验收指标。请按以下规则撰写：

1. **按成果组织**：以 deliverables 中的每项成果为一级条目，在其下列出该成果对应的全部考核指标。
2. **每条指标的结构**：
   - **成果类型**：该成果的类别（如：系统/平台/工具/数据集/模型/方法/标准/论文/专利/报告——依课题实际选取）
   - **量化指标**：必须是可由第三方独立验证的具体数值指标。禁止使用"提升XX能力""优化XX性能"等无法度量的表述。指标须带具体数值与度量单位或口径。
   - **立项时基准值**：当前现状值；若属开创性工作无现有基准，填"无（开创性）"。
   - **完成时目标值**：课题结束时须达到的目标数值。
   - **考核方式**：说明如何验证该指标已达成（如：第三方检测报告 / 开源代码仓库+运行截图 / 学术会议录用通知 / 专利受理通知书 / 用户测试报告 / 独立评审意见 等）。
3. **全覆盖检查**：完成后逐条核对——资助方的每一项硬性交付要求（requirements）是否都被至少一条成果及其指标所覆盖。若有遗漏，补充。
4. **指标数值合理性**：指标目标值应基于课题技术路线可合理预期的水平，不虚高、不保守。
5. **呈现方式**：每项成果的指标用简洁的文字或分点呈现，立项时值与完成时值随文写明（如"由0项增至1项"）；**不要**用"指标属性 | 内容说明"这类两列竖表把少量信息硬塞进表格。仅当确有多行多列的可比数据（如多个算子×多种硬件的指标矩阵）时才使用表格。"""

MOD_NOVELTY = """## 【追加要求：创新点对比写法】

本章涉及创新点。请按以下规则撰写：

1. **对比句式**：每个创新点必须写成"现有做法如何 → 本课题如何"的对比结构，明确指出：
   - 现有主流做法/技术的具体局限（点名具体方法或工作，给出其局限的量化表现或定性描述）
   - 本课题的改进思路与预期效果（给出具体技术手段与预期改进幅度或新能力）
2. **参考创新角度**：优先参考以下预定义的创新角度（可调整措辞，但核心对比逻辑须保留）：
   ${novelty_angles}
3. **层次覆盖**：创新点应覆盖至少两个层面（如：方法/算法层面、系统/工程层面、应用/场景层面、理论/模型层面——依课题实际选取）。
4. **避免自说自话**：不得用"首次提出""创新性地提出"等自封式表述代替实质对比。"""

MOD_TIMELINE = """## 【追加要求：进度阶段量化】

本章涉及计划进度与里程碑。请按以下规则撰写：

1. **阶段划分**：根据硬性约束中的项目周期（见 ${constraints}），将课题划分为合理的阶段（通常 2-4 个阶段）。每个阶段标注起止时间。
2. **每阶段内容**：
   - **拟完成的核心任务**：具体列出本阶段要完成的研发任务（与研发内容/技术方案章节呼应）。
   - **阶段性量化指标**：本阶段结束时应达到的量化指标（与考核指标章节呼应，是最终考核指标的阶段性分解）。
   - **阶段交付物**：本阶段产出的具体交付物名称。
3. **递进逻辑**：各阶段之间应体现清晰的递进关系（如：基础构建→核心攻关→集成验证→优化交付），而非简单的时间切片。
4. **指标呼应**：阶段指标的最终汇总应与考核指标章节的目标值一致，不得出现矛盾。"""

MOD_ALGORITHM = """## 【追加要求：方法描述的语体——凝练、专业、致密】

本章方法/关键技术的质量不在"要素齐全"，而在表达本身：像本领域资深专家落笔，用尽量少的字承载尽量多的真实技术信息。
- 每项核心方法点名它由哪些具体技术组合而成、各自的作用或所保证的性质（可用括号紧跟补充限定），直指"怎么做"与"达到什么"。
- 不铺垫背景、不解释常识、不堆砌只有名字的技术，也不用数学公式充深度。
- 让读者读完知道"具体怎么做、为何可行"，而非只知道"要做什么"。
把最核心的一两项写透、写专业，其余从简。"""

# (trigger keyword, modifier text)
# Keywords are matched against the section NAME as well as its required elements.
# Name matching is what makes MOD_KPI fire at all: a KPI section is reliably called
# 考核指标, while its required list tends to spell the demand out ("每条量化(…带数值)")
# without ever using the phrase. Note "量化指标" is deliberately NOT a MOD_KPI
# trigger — it appears inside the approach section's "分阶段计划进度(含每阶段量化
# 指标)", where MOD_TIMELINE already covers stage-level quantification.
MODIFIERS = [
    (["考核指标", "验收指标", "交付指标", "指标值", "考核方式"], MOD_KPI),
    (["创新点", "创新性", "技术创新", "理论创新"], MOD_NOVELTY),
    (["计划进度", "里程碑", "时间安排", "阶段划分", "进度安排", "进度"], MOD_TIMELINE),
    (["关键技术", "技术方案", "技术路线", "研发内容", "研究内容"], MOD_ALGORITHM),
]


# ─────────────────────────── helpers ───────────────────────────

def modifiers_for(required_elements: str, section_name: str = "") -> str:
    """Return concatenated modifier text triggered by this section.

    Matches the section name as well as its required elements. Matching the
    required text alone let MOD_KPI miss the very section it was written for:
    task_001's 考核指标 lists "每条量化(数量/技术/应用指标,带数值)", which contains
    no trigger phrase, so that section was drafted with no formatting rules at all.
    """
    haystack = f"{section_name}\n{required_elements}"
    out: List[str] = []
    for keywords, text in MODIFIERS:
        if any(k in haystack for k in keywords):
            out.append(text)
    return ("\n\n" + "\n\n".join(out)) if out else ""


def fill(template_text: str, variables: Dict[str, Any]) -> str:
    """Substitute ${identifier} placeholders; leave JSON braces and unknown
    (e.g. Chinese) placeholders untouched."""
    return Template(template_text).safe_substitute(variables)
