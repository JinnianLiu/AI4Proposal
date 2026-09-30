# 实验 1：脚本使用说明

方案见 [PLAN.md](PLAN.md)。本文说明每个脚本怎么调用、产出放在哪里。想先试一篇，直接看第 2 节"单次运行"。所有命令都在仓库根目录执行，并统一用 `uv run`。

## 0. 准备

### 环境变量

| 变量 | 用途 | 谁要用 |
|---|---|---|
| `AI4PROPOSAL_API_KEY` | DeepSeek 密钥 | 生成（C1–C4 都用 `deepseek-flash`）、DeepSeek 评委 |
| `AI4PROPOSAL_BASE_URL` | 默认 `https://api.deepseek.com`，一般不用改 | 生成 |
| `DASHSCOPE_API_KEY` | Qwen 评委密钥（阿里云百炼 DashScope） | 评审 |
| `GPT_IMAGE_API_KEY` | Gemini 评委密钥（经 chatanywhere 访问） | 评审 |
| `PYTHONUTF8=1` | Windows 下避免中文乱码 | 全部 |

生成用的模型固定为 `deepseek-flash`，由脚本自动设置，不读 `AI4PROPOSAL_MODEL`。

PowerShell 示例：

```powershell
$env:PYTHONUTF8 = "1"
$env:AI4PROPOSAL_API_KEY = "sk-..."
$env:DASHSCOPE_API_KEY = "sk-..."
$env:GPT_IMAGE_API_KEY = "sk-..."
```

### 评委配置

在 [judges.json](judges.json) 里填写三个评委的模型 id、接口地址和密钥变量名；密钥本身不写进文件。如果 Qwen 或 Gemini 通过其他平台或代理访问，只需改对应的 `base_url`。

### C1 需要的命令行工具

- C1-CC：`claude`（Claude Code）
- C1-CX：`codex`（Codex CLI）

两者都要能在命令行直接调用（在 PATH 上）。不跑哪个版本，就不需要装哪个。

## 1. 自检（不花钱，改完代码先跑）

```powershell
uv run python tests/test_experiments.py    # 实验脚本：提示词、task、规范化、作废判定、汇总
uv run python tests/test_evaluation.py     # 评审框架
```

两个都应输出 `PASS`。

## 2. 单次运行（先试一篇）

`run_one.py` 用一条命令完成三件事：为一个条件、一个 task 生成一篇，再交给评委评审，最后打印结果。适合单独试某个条件或某个 task；批量运行请用下面第 3–4 节的脚本。

```powershell
# C4 × task_001，交给 judges.json 里的全部评委
uv run python experiments/run_one.py --condition C4 --task task_001

# 只用 DeepSeek 评委
uv run python experiments/run_one.py --condition C3 --task task_008 --judges deepseek

# 只生成、不评审
uv run python experiments/run_one.py --condition C1-CC --task task_001 --no-judge

# 已有结果也重做（重新生成并重新评审）
uv run python experiments/run_one.py --condition C4 --task task_001 --judges deepseek --force
```

| 参数 | 说明 |
|---|---|
| `--condition` | 必填：`C1-CC`、`C1-CX`、`C2`、`C3`、`C4` 之一 |
| `--task` | 必填：`task_001` 至 `task_012` |
| `--rep` | 生成的重复编号，默认 1 |
| `--judges` | 评委名（见 `judges.json`），可以写多个；不写则用全部评委 |
| `--judge-reps` | 评审的重复编号，默认 `1` |
| `--no-judge` | 只生成，不评审 |
| `--force` | 已有有效结果也重做 |
| `--max-output-tokens` / `--c4-deadline` | 只对 C4 生效，默认 384000 token 和 3600 秒 |

打印内容：
- **生成**：状态（有效 / 作废及原因）、字数、耗时、调用次数与 token、`finish_reason`（C4）、框架版本、轮数和续跑次数（C1）、缺失的章节、正文路径；
- **评审**：每个评委的总分、判定和 7 个维度的分数。

已有有效的生成或评审结果时会直接复用，不再调用 API；需要重做就加 `--force`。结果写在批量脚本相同的位置，会计入实验，也会被 `collect.py` 汇总。

示例输出：

```
[generate] reuse existing valid run C4__task_001__r1  (--force to regenerate)

  run_id        C4__task_001__r1
  status        valid
  chars         7052
  elapsed       67.6 s
  llm calls     1  tokens in/out 2396/12868
  finish_reason stop
  proposal      ...\runs\exp1\gen\C4\task_001\r1\proposal.md

[judge] ...
  deepseek  j1   79.6  recommend_submit  scie=8 inno=7 impa=8 alig=9 feas=8 clar=8 comp=8
```

退出码：生成有效且所有评审都成功时为 0，否则为 1。

## 3. 生成（批量）

### C2、C3、C4

```powershell
# 全部 12 个 task、三个条件
uv run python experiments/generate.py

# 指定条件和 task（冒烟检查用）
uv run python experiments/generate.py --conditions C2 C3 C4 --tasks task_001 task_003

# 只跑 C4
uv run python experiments/generate.py --conditions C4
```

| 参数 | 说明 |
|---|---|
| `--conditions` | `C2`（流水线去蓝图）、`C3`（全系统）、`C4`（单次调用）中的任意几个，默认三个都跑 |
| `--tasks` | 默认全部 12 个 |
| `--rep` | 生成的重复编号，默认 1。再跑一轮用 `--rep 2` |
| `--max-output-tokens` | 只对 C4 生效，默认 384000，即 deepseek-flash 的最大输出长度 |
| `--c4-deadline` | 只对 C4 生效，单次调用的墙钟时限，默认 3600 秒 |
| `--attempts` | 一次运行作废后最多重试几次，默认 3 |
| `--force` | 已有有效结果也重跑 |

**C4 的输出上限**默认按模型的最大值请求。如果用服务端默认值，C4 可能被人为截断，而分章写作的 C2、C3 不会。每篇 C4 的 `finish_reason` 都会记录下来，`length` 表示被截断。

### C1（两个框架）

```powershell
uv run python experiments/run_c1.py                                   # 两个框架、全部 task
uv run python experiments/run_c1.py --frameworks CC --tasks task_001  # 只跑 Claude Code 版
uv run python experiments/run_c1.py --frameworks CX --tasks task_001  # 只跑 Codex 版
```

参数 `--tasks`、`--rep`、`--attempts`、`--force` 与上面相同。智能体在各自空的工作目录里运行，全文写进 `proposal.md`。如果写完缺章，脚本会用固定语句续跑，最多 2 次。

### 中断与续跑

两个生成脚本都可以随时中断后重新执行：已经有效的运行会跳过，作废的运行会重跑。

## 4. 评审（批量）

```powershell
# 先测连通性：每个评委发一次很短的请求，检查密钥、地址和模型 id
uv run python experiments/judge.py --check

# 全部有效运行 × 全部评委，每个评委评 1 次
uv run python experiments/judge.py

# 用于估计评委噪声的重复评估（例如只对部分 task 再评一次）
uv run python experiments/judge.py --reps 2 --tasks task_001 task_007 task_008

# 只跑某个评委或某些条件
uv run python experiments/judge.py --judges gemini --conditions C3 C4
```

| 参数 | 说明 |
|---|---|
| `--judges` | `judges.json` 里的名字，默认全部 |
| `--conditions` / `--tasks` | 筛选要评的运行 |
| `--reps` | 评审的重复编号，默认 `1`；`--reps 1 2` 表示两次都要 |
| `--workers` | 同时进行的评审团数量，默认 4。遇到限流就调小 |

- 只评 `status` 为 `valid` 的运行。
- 评审不截断、不检索、不看配图。
- 任何一次评委调用失败或某个维度没打分，这次评审就作废重试；连续 3 次失败记为 `missing`，不会用兜底分，也不会把失败维度的权重分给其他维度。
- 已完成的评审会跳过，中断后可以直接重跑。

## 5. 汇总成表

```powershell
uv run python experiments/collect.py                     # -> runs/exp1/results.xlsx
uv run python experiments/collect.py D:/somewhere/x.xlsx # 指定输出位置
```

- `tasks`、`runs`、`judge_scores`、`summary` 四张表每次都根据记录重新生成。
- `human_scores`、`blind_map`、`figure_human` 三张是人工填写的表（表头黄色），重跑时原样保留。
- `summary` 里的均分是公式，在 Excel 或 LibreOffice 里打开后才会显示数值。

## 6. 其他

- **只看渲染好的 C1/C4 提示词**：`uv run python experiments/prompts_render.py [task_xxx]`，输出到 `runs/exp1/prompts/`。
- **单独跑流水线的 C2**：`uv run python scripts/run_pipeline.py --task experiments/tasks/task_001.json --no-blueprint --figures off`。

## 产出位置

所有产出都在 `runs/exp1/` 下（该目录不入库）：

```
runs/exp1/
├── gen/<条件>/<task>/r<k>/
│   ├── raw.md          条件的原始产出，不改动
│   ├── proposal.md     规范化后的正文，评委读的是这份
│   ├── task.json       当时使用的冻结 task
│   ├── run.json        运行记录：状态、作废原因、字数、调用次数、token、耗时、finish_reason 等
│   ├── pipeline/       C2、C3：流水线原始输出（C3 含图件规划，供配图评估使用）
│   ├── pipeline.log    C2、C3：流水线日志
│   ├── work/           C1：智能体的工作目录
│   ├── codex_home/     C1-CX：本次运行专用的 Codex 配置与会话（自动生成）
│   └── transcript.jsonl  C1：完整过程记录（含工具调用）
├── judge/<评委>/<条件>/<task>/r<k>_j<m>.json   一次完整评审团的结果
├── prompts/            渲染好的 C1/C4 提示词
├── logs/               后台批量运行的日志
└── results.xlsx        collect.py 生成的结果表
```

## 冻结的输入

- task：`experiments/tasks/`（12 个，来源与待核对项见 [tasks/SOURCES.md](tasks/SOURCES.md)）
- C1/C4 统一提示词：[prompts/single_system.md](prompts/single_system.md)。渲染脚本直接解析这份文档里的代码块，改文档就等于改实际发出的提示词。
