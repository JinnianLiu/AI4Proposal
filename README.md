# AI4Proposal

**面向真实资助指南的科研项目申请书生成与评审系统。**

输入一份从**真实公开指南**转写的 task（含硬性交付物、约束、资格与溯源），系统产出申请书的**核心研究内容**，并用一套写死细则的多评委框架给它打分。

```
task.json ──▶ 蓝图 ──▶ 逐章撰写 ──▶ 配图 ──▶ proposal_final.md ──▶ .docx
                                                   │
                                                   └──▶ 7 维评审 ──▶ evaluation.json
```

写作与评审是**两条独立的链路**：评审不回喂给写作，评分也不依赖任何标准答案。

---

## 1. 快速开始

### 安装

```bash
uv sync
```

Python ≥ 3.9。依赖 `openai`、`pypandoc-binary`、`python-docx`；检索模块只用标准库。

本项目用 **uv** 管理环境，命令一律走 `uv run`（否则可能落到别的 Python 环境、缺依赖）：

```bash
uv run python scripts/run_pipeline.py --task cases/tasks/task_001.json
```

下文命令为简洁起见省略了 `uv run` 前缀。

### 配置

所有配置走环境变量，源码中不含任何密钥。

```bash
export AI4PROPOSAL_API_KEY=sk-...
export AI4PROPOSAL_BASE_URL=https://api.deepseek.com
export AI4PROPOSAL_MODEL=deepseek-v4-pro
export AI4PROPOSAL_IMAGE_API_KEY=sk-...        # 出图（可选）
```

| 变量 | 必需 | 默认 | 说明 |
|---|:--:|---|---|
| `AI4PROPOSAL_API_KEY` | ✅ | — | 文本 LLM 密钥 |
| `AI4PROPOSAL_BASE_URL` | | `https://api.deepseek.com` | OpenAI 兼容端点 |
| `AI4PROPOSAL_MODEL` | | `deepseek-chat` | 正式跑建议 `deepseek-v4-pro` |
| `AI4PROPOSAL_TIMEOUT_SECONDS` | | `300` | 单次调用超时；长章节实测可达 200s |
| `AI4PROPOSAL_SDK_RETRIES` | | `1` | openai SDK 内部重试次数。SDK 默认 2，会让一次可见调用静默变成三次请求 |
| `AI4PROPOSAL_QUIET_LLM` | | — | 置 `1` 关闭每次调用的 `[llm] <端点> <耗时> <字数>` 日志 |
| `AI4PROPOSAL_CALL_DEADLINE_SECONDS` | | `600` | 单次调用的**墙钟**上限。SDK 的 timeout 是空闲超时，服务端持续吐字节就永不触发 |
| `AI4PROPOSAL_VISION_MODEL` | | `deepseek-v4-flash-vision-exp` | 配图审查用的多模态模型；置空则关闭配图审查 |
| `AI4PROPOSAL_IMAGE_API_KEY` | | — | 留空则跳过出图，退化为文字占位符 |
| `AI4PROPOSAL_IMAGE_BASE_URL` | | `https://api.chatanywhere.tech/v1` | |
| `AI4PROPOSAL_IMAGE_MODEL` | | `gpt-image-2` | |
| `AI4PROPOSAL_FIGURE_PALETTE` | | 内置学术配色 | 逗号分隔的 HEX 色板（≤6 个），如 `#E3F2FD,#1E88E5` |
| `AI4PROPOSAL_MAILTO` | | — | OpenAlex 礼貌池邮箱，检索更快 |
| `AI4PROPOSAL_S2_API_KEY` | | — | 改用 Semantic Scholar 时的配额密钥 |
| `AI4PROPOSAL_INSECURE_TLS` | | — | 设 `1` 则检索跳过证书校验（见下）|

Windows PowerShell 下建议同时设 `$env:PYTHONUTF8=1`。

### 跑一遍

```bash
# 生成（含出图）
python scripts/run_pipeline.py --task cases/tasks/task_001.json

# 评审
python scripts/evaluate.py outputs/task_001/proposal_final.md --evidence

# 转 Word
python scripts/md_to_docx.py outputs/task_001/proposal_final.md
```

---

## 2. 写作流水线

`scripts/run_pipeline.py`

| 步骤 | 做什么 |
|---|---|
| **Step 0 蓝图** | 从 task 提炼统一主线 thesis、可交付成果体系、关键方法、创新角度，写入 `blueprint.json`。后续每章回扣它，避免模块平铺 |
| **逐章撰写** | 按 `task.structure.core_sections` 逐节生成，**一次成稿**（不在流水线内做 review-revise）。每章带上前文摘要，防重复 |
| **出图** | 正文中的 `[figure: 图注 \|\| 图位描述]` 标记 → **两阶段**：LLM 规划图件（构图类型 / 论点 / 中文图题 / 英文生图提示词）→ 图像模型渲染 → 回填 Markdown。图内只出极短英文标签，中文只出现在图题与图注 |
| **评分** | 可选，`--judge rubric` 直接串联评审框架 |

### 提示词设计

`src/ai4proposal/writer_prompts.py` —— **域中立**：固定文本只描述*形式*与*质量标准*，全部实质内容经 `${...}`（`string.Template`）注入。因此同一套提示词可跨学科使用，换 task 即可。

组成：Step-0 蓝图 / 结构规划器 / 写作通则 / 通用 writer / 格式修饰符（考核指标结构化、创新点对比写法、进度阶段量化、方法语体）。

每章**一次成稿** —— 流水线内不做评审-改写循环。早期设计过逐段 review-revise，已废弃，相应提示词已删除。

修饰符按章节的 `required` 字段**自动触发** —— 例如某章要求"每条量化"，`MOD_KPI` 才会追加进 system prompt。

### 章节结构从哪来

优先用 `task.structure.core_sections`（指南强制的行文结构）。task 未声明时，由 `STRUCTURE_PLANNER` 现场规划，而非套用硬编码模板。

### 常用参数

```bash
--task <path|id>          # cases/tasks/ 下可只给 id
--sections N              # 只跑前 N 章，调试用
--figures {go,dry,off}    # dry = 只规划不生图，人工确认后再补
--figures-from <dir>      # 用已确认的图件规划补生图，不重跑文本与规划
--max-figures N           # 默认 5
--judge rubric            # 生成后直接评审
--judge-evidence          # 评审时开外部检索
```

`--figures dry` → 人工看图件规划 → `--figures-from` 是推荐的出图工作流，避免图不满意就得重跑全文。

---

## 3. 评审框架

`src/ai4proposal/evaluation.py` · `scripts/evaluate.py`

**不比对标准答案。** 每个维度写死五档锚点（9-10 / 7-8 / 5-6 / 3-4 / 1-2），评委是"选档位"而非凭印象打分 —— 这是保证跨本子可比的手段。

**四位专家 + 主席。** 每位专家独立通读全文，只对自己负责的维度打分，彼此不见对方结论。主席只汇总定性意见，不打分。

**总分与判定由代码算，不由 LLM 给。**

| 维度 | 权重 | 评委 | 关注 |
|---|---:|---|---|
| `scientific_quality` 科学质量 | 0.20 | science ＋检索 | 问题是否明确、重要、可证伪；有无机制/理论层面价值 |
| `innovation` 创新性 | 0.18 | science ＋检索 | 是否超越已有工作的组合、调参与工程集成 |
| `feasibility` 可行性 | 0.16 | feasibility | 路线、实验设计、数据、周期、指标可达性 |
| `alignment` 一致性 | 0.14 | value | 目标—内容—路线—创新点—成果是否闭环、是否契合指南 |
| `impact` 学术影响 | 0.12 | value | 意义论述是否可信，还是可套用到任意课题 |
| `compliance` 规范性 | 0.11 | writing | 结构完整性、占位符残留、术语规范 |
| `clarity` 清晰度 | 0.09 | writing | 概念、编号、指标、时间表是否前后一致 |

权重在 `WEIGHTS` 一处定义，改它即可，聚合逻辑自动跟随。某维解析失败时其权重**重分配**给其余维度，而非记 0 分。

### 配图审查（多模态，不计入总分）

上面四位评委只读得到 `![fig_01](figures/fig_01.png)` 和一行图注 —— 图是空白、乱码、与图注不符还是画了编造的数值，他们一律看不出来。

第五位评委是多模态的：**逐图输入 图 + 图注 + 该图所在的正文段落**，只考察两件事 ——

- **与正文的匹配度**：只看这张图能否抓住这段正文的核心内容（不是"图里的元素在正文出现过没有"）
- **图片表现**：标签可读性、乱码、布局遮挡、箭头指向、编造数值、无关装饰

**只出结构化发现，不打分、不进硬闸**（`figure_findings`）—— 刻意如此：它没有可比的分档锚点，硬塞进加权会污染跨本子可比性。

有渲染出的图才会触发；`AI4PROPOSAL_VISION_MODEL` 置空即关闭。`--figures off` 可单次跳过。

### 硬闸

分数再高，命中以下任一项也不会给出 `recommend_submit`（可通过修改补救）：

- 指南硬性要求未在正文落实
- 缺失被要求的章节
- 存在占位符 / 模板残留

`alignment < 4` **直接 reject**，不论总分多少 —— 答非所问不是改稿能补救的。单靠权重时，一份完全跑题的文档仍能拿到 64/100。

判定取值：`recommend_submit` / `revise_resubmit` / `reject`（总分 < 6.0 或 alignment 不及格）。

### 维度隔离

每位评委的 prompt 里都写明：只对自己负责的维度打分，**不属于自己维度的缺陷一律不得影响分数**，尤其是跑题（那是 `alignment` 的职责）。

这条是实测逼出来的：早期版本拿一份跑题文档去评，文字评委把 `clarity` 打到 2 分、`compliance` 打到 3 分，理由全是"与课题设定割裂"—— 形式评委在替 alignment 扣分，等于同一个缺陷被罚了三次。加上隔离指令后，同一文档的 `clarity` 回到 8。

### 评分范围限定

流水线只产出**核心研究内容**（目标 / 考核指标 / 研发内容·关键技术·创新点 / 技术方案·路线·进度 / 预期成果）。团队、研究基础、经费预算、设备条件、参考文献列表**不在生成范围**。

细则据此收窄，且每位评委的 prompt 里都有一条显式指令：这些内容的缺失不得作为扣分理由，也不得写进 weaknesses —— 没有这条，模型会自发地"发现"这些缺失并反复扣分。

### 外部检索

`src/ai4proposal/evidence.py`。一次 LLM 调用抽出最该核查的论断（novelty / metric / method），到 OpenAlex（免密钥）检索真实论文，把证据卡注入 **science 评委**。

检索只提供**证据**，不提供结论，判断权始终在评委。检索失败、超时或关闭时退化为中性占位符，评审照常进行。

TLS 证书**默认校验**；仅当证书链确实失败（公司网/VPN 拆包）才自动降级为不校验并打印告警，其他错误（如 429）照常走重试。设 `AI4PROPOSAL_INSECURE_TLS=1` 可直接跳过校验。

### 输出

除七个维度分数外，`evaluation.json` 还结构化收集：过度声称、识别到的科学假设、指南要求逐条覆盖情况、跑题内容、存疑指标、风险预案、章节 checklist、占位符、约束违反、配图审查发现（`figure_findings`）。

```bash
python scripts/evaluate.py <md>              # task.json 自动从同目录读取
python scripts/evaluate.py <md> --evidence   # 开外部检索
python scripts/evaluate.py <md> --figures off  # 跳过配图审查
python scripts/evaluate.py --show-rubric     # 只看细则，不消耗 token
python tests/test_evaluation.py            # 离线自检，无需密钥与网络
```

---

## 4. Task 数据

### `cases/tasks/` —— 指南锚定的 task（当前使用）

6 个跨学科 task，均从**真实公开资助指南**转写，带硬约束与溯源。详见 [cases/tasks/INDEX.md](cases/tasks/INDEX.md)。

| task | 领域 | 资助方 |
|---|---|---|
| task_001 | AI / 系统软件 | 众智 FlagOS 加速计划—智源学者 |
| task_002 | 人文（历史/文献学） | 国家社科基金重大项目 |
| task_003 | 生物医学 | Wellcome Discovery Awards |
| task_004 | 合成生物学 | 国家重点研发计划重点专项 |
| task_005 | 社会科学 | 教育部人文社科一般项目 |
| task_006 | 材料 / 物理 | NSF DMREF |

关键字段：`program` / `direction` / `budget.is_cap` / `eligibility` / `requirements`（硬交付物）/ `constraints`（硬约束）/ `structure`（指南强制的行文结构）/ `provenance`（溯源）。

`provenance.origin_type` 全部为 `public_guideline_plus_expert_reconstruction`：资助方、额度、周期、资格、交付物来自官方原文；具体选题为该方向下的合理重构（指南本身不指定课题）。

### `legacy/research_topics/` —— 合成选题（继承物，不参与当前流水线）

88 个纯合成的学术选题，由项目前任维护者用 `legacy/build_topics.py` 生成、`legacy/fix_references.py` 替换过参考文献。它们**没有** `structure` / `requirements` / `constraints`，当前流水线读不了，也不作为评审样本。与生成它们的两个脚本一起归入 `legacy/`，`cases/` 下只留真实指南锚定的 task。

---

## 5. 项目结构

目录按**职责**分层：`src/` 是库，`scripts/` 是日常使用的生产 CLI，`tools/` 是消耗 API 的开发工具，`tests/` 是离线自检，`legacy/` 是继承下来、已完成使命的脚本。

```
src/ai4proposal/        核心库
├── llm.py              OpenAI 兼容后端 · backend_from_env / generate_with_retry / parse_json
│                       / cheap_backend / vision_backend
├── writer_prompts.py   域中立提示词集（蓝图 / 结构规划 / 通则 / writer / 格式修饰符）
├── evaluation.py       7 维细则 · 4 评委 + 主席 · 代码算总分与硬闸
├── evidence.py         论断抽取 + OpenAlex/arXiv 检索 + LLM 重排 → 证据卡
├── image_gen.py        [figure:] 标记 → 图件规划（JSON）→ PNG（两阶段）
└── intake.py           指南文档 → call → 方向/选题 → task（web 入口用）

scripts/                生产 CLI
├── run_pipeline.py     写作流水线
├── evaluate.py         评审（对 Markdown 打分）
└── md_to_docx.py       Markdown → Word（pandoc + 中文样式模板）

tools/                  开发与校准（会消耗 API 额度）
└── measure_variance.py 同一文档重复评分，量化噪声底线

tests/                  离线自检（无需密钥与网络）
└── test_evaluation.py

legacy/                 继承物，保留备查，不参与当前流水线
├── build_topics.py     合成选题生成
├── fix_references.py   一次性参考文献替换，已执行完毕
└── research_topics/    88 个合成选题（上面两个脚本的产物）

cases/tasks/            指南锚定 task + INDEX.md
assets/reference.docx   Word 样式模板（宋体正文 / 黑体标题）
outputs/                生成产物（不入库）
```

### 产物布局

```
outputs/<task_id>/
├── proposal_final.md      完整申请书
├── blueprint.json         Step-0 蓝图
├── <section>.md           各章单独文件
├── figures/fig_*.png      配图
├── figure_plans.json      图件规划（供 --figures-from 复用）
├── figures/fig_*.plan.json 每张图的规划留底
├── figure_manifest.json
├── task.json              输入快照
├── result.json            运行元数据
└── evaluation.json        评审结果（跑过 evaluate.py 后）
```

---

## 6. 当前状态

| 部分 | 状态 |
|---|---|
| 写作流水线 | 基本完善 |
| 文本 / 生图 prompt | 待调优，等待领域专家意见 |
| 现行流水线的产出 | **task_001**（多轮，最新 v13）、**task_002**（v1）、**task_003**（v2）|
| task_004 – 006 | `outputs/` 下的产出来自**上一代流水线**，无 `blueprint.json`，不代表现状，需重跑 |
| 评审框架 | 已重做完成，权重为初版，待跨学科样本后微调 |
| `md_to_docx.py` | 待增强兼容性，需先有新产出以暴露排版问题 |

产出目录是否由现行流水线生成，看有无 `blueprint.json` 即可判断。

**不在仓库内**：作为写作质量标杆的中标本子（gold）及其摘录属非公开材料，已在 `.gitignore` 中排除。

---

## License

MIT
