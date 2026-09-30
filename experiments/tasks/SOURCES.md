# 新增 task 的来源与查询记录

查询日期：2026-09-30。6 个 task 草稿等待核对：
- task_007–010：由我查招标信息新建。
- task_011、task_012：由用户提供，我按现行指南做了补充和格式转换。

## 选题原则

- **与现有 task 同一标准**：资助方、额度、周期、资格、交付物、行文结构都转写自现行官方指南；具体选题是在该方向下的合理重构，与现有 6 个 task 一样标为 `public_guideline_plus_expert_reconstruction`。
- **计算机 × 另一学科**：两名人工评审都是计算机背景，所以 4 个新 task 都选计算机与另一学科的交叉方向。这样既覆盖了新学科（生态、医学、社会科学、地学），又在人工评审能判断的范围内。
- **指南给定结构**：4 个都有官方规定的正文结构，这样评审的"期望章节"来自指南，而不是来自流水线（见 PLAN.md §4）。
- **ogrants 的用法**：ogrants 上的项目大多是 2010–2017 年的，对应的招标早已更新。所以 ogrants 只用来选资助计划、参考真实写法（记录在 `provenance.reference_proposals` 字段），指南事实一律取自现行官方页面。选题不照搬 ogrants 上的原项目，以免模型见过原文。

## 分布

| task | 资助计划 | 语言 | 交叉方向 | ogrants 参照 |
|---|---|---|---|---|
| 007 | NSF CAREER（NSF 22-586） | en | 机器学习 × 生态监测 | bahlai_christie_2021、zare_alina_2013 |
| 008 | NIH R21（PA-25-304） | en | 机器学习 × 神经退行性疾病 | klein_arno_2016e、klein_arno_2016c |
| 009 | ERC Starting Grant（ERC-2027-StG） | en | 计算社会科学 × 生成式 AI | bekkers_rene_2011 |
| 010 | 国家自然科学基金面上项目（2026） | zh | 深度学习 × 气象临近预报 | 无（ogrants 没有国内项目） |

| 011 | 国家自然科学基金重大研究计划（2026）· 重点支持项目 | zh | 航空宇航：流动控制 | 用户提供 |
| 012 | "脑科学与类脑研究"国家科技重大专项（2026） | zh | 脑机接口 × 认知神经科学 | 用户提供 |

12 个 task 中，中文 7 个、英文 5 个。

## 各 task 的来源与待核对项

### task_007 · NSF CAREER

| 事实 | 来源 | 状态 |
|---|---|---|
| 最低额度 $400,000 / 5 年（BIO、ENG、OPP 为 $500,000） | [NSF 22-586 solicitation](https://www.nsf.gov/funding/opportunities/career-faculty-early-career-development-program/nsf22-586/solicitation) | 已读 |
| Project Description 的五项必备内容、15 页上限、Departmental Letter | 同上 | 已读，但五项的原文被截断 |
| 资格、每个竞赛周期只能提交一份、不设 co-PI | [CAREER 项目页](https://www.nsf.gov/funding/opportunities/career-faculty-early-career-development-program) | 已读 |
| 2027 年截止日期为 7 月 28 日 | 同上 | 已读 |
| Broader Impacts 必须单列成节并使用该标题 | NSF PAPPG | 凭已有知识，本次未打开原文 |

**待核对**：22-586 是否仍是 2026–2027 周期的现行 solicitation；五项必备内容的完整原文。

### task_008 · NIH R21

| 事实 | 来源 | 状态 |
|---|---|---|
| 探索性研究定位；项目期不超过 2 年；直接费用合计不超过 $275,000，单年不超过 $200,000；不要求预实验数据；不允许临床试验；三个评审因素 | [PA-25-304](https://grants.nih.gov/grants/guide/pa-files/PA-25-304.html) | 已读 |
| Specific Aims 1 页；R21 的 Research Strategy 6 页 | [NIH 页数限制表](https://grants.nih.gov/grants/how-to-apply-application-guide/format-and-write/page-limits.htm) | 已读 |
| Research Strategy 分为 Significance / Innovation / Approach | NIH How to Apply 申请指南 | 凭已有知识，本次未打开原文 |

**待核对**：Research Strategy 三个小节在现行申请指南中的写法；是否要在 task 里点名具体的公开队列（例如 PPMI）。

### task_009 · ERC Starting Grant

| 事实 | 来源 | 状态 |
|---|---|---|
| 最高 150 万欧元 / 5 年，可追加 100 万或 200 万；卓越性是唯一评审标准；2027 轮 7 月 22 日开放、10 月 14 日截止 | [ERC StG 页面](https://erc.europa.eu/apply-grant/starting-grant) | 已读 |
| 2026 年起正文拆为 Part I（5 页：研究现状、科学问题、目标、总体研究策略）和 Part II（StG 7 页：方法、工作计划、风险与应对、资源说明）；第一轮评审只读 Part I | [CASRAI 解读](https://casrai.org/news/erc-2026-scientific-proposal-part-i-part-ii) | 二手来源 |
| 官方写作指南 | [UZH ERC-2026 Proposal Writing Guide](https://www.research.uzh.ch/dam/jcr:3444dcd8-27bc-480e-bb88-ad2a28c37203/ERC-2026_StG-CoG_Proposal_Writing_Guide.pdf) | PDF 有密码，未能读取 |

**待核对**：Part I / Part II 的官方标题与页数，以 ERC-2027 Information for Applicants 为准；博士毕业年限（ERC 页面摘要写的是 0–10 年，往年是 2–7 年）。

**本 task 自己的设计**：把每个 Part 拆成两节，并按页数折算了字数上限，这些不是 ERC 的规定，只是为了让流水线有章节可写。

### task_010 · 国家自然科学基金面上项目

| 事实 | 来源 | 状态 |
|---|---|---|
| 2026 年面上项目与青年 C 类申请书改版：正文分立项依据、研究内容、研究基础三部分；研究内容不设预设提纲；正文原则上不超过 30 页 | [基金委《关注！！！2026年申请书改版》](https://www.nsfc.gov.cn/p1/3381/2821/99242.html) | 已读 |
| 集中接收期为 3 月 1 日至 3 月 20 日；研究期限由系统自动生成 | [基金委 2026 年申请通告](https://www.nsfc.gov.cn/p1/3381/2824/99667.html) | 已读 |
| 改版报道 | [科学网](https://news.sciencenet.cn/htmlnews/2026/1/558943.shtm) | 已读 |

**待核对**：
- **资助期限、资助强度、申请条件都没有查到官方原文**，所以 task 里 `budget` 的数值字段是空的。往年惯例是 4 年、约 50 万元，但未经核实，请以《2026年度国家自然科学基金项目指南》原文为准。
- 新版正文模板中立项依据部分是否还有细化的提示。

**本 task 自己的设计**：两部分的 `required` 要点是为了让写作可执行而做的归纳，不是基金委原文。

### task_011 · 多物理场高效飞行重大研究计划（用户提供）

**原件**：以整个重大研究计划为选题（标题就是计划名称），7 个章节覆盖全部三个核心科学问题。问题有两个：一份申请书不可能覆盖整个计划；结构也是重构的，不是官方模板。

**处理**：按 2026 年度项目指南收窄到一个**重点支持项目**方向——"基于高效流动控制的跨域变构飞行器动态激波干扰抑制机理"，对应原件第 3 条挑战和第二个核心科学问题。background 和前两条 challenges 沿用原件。

| 事实 | 来源 | 状态 |
|---|---|---|
| 科学目标、三个核心科学问题；重点支持项目不超过 8 项、直接费用约 200 万元/项、3 年；研究期限 2027-01-01 至 2029-12-31；合作单位不超过 2 个；须说明符合指南方向、对核心科学问题的贡献、与相关项目的区别与联系；申请时间 2026 年 3 月 1 日至 20 日 | [基金委 2026 年度该计划项目指南](https://www.nsfc.gov.cn/p1/3381/2824/100395.html) | 已读 |
| 8 个重点支持方向 | 同上，另见[检索结果摘要](https://www.hunan.gov.cn/zqt/xmsb/202601/t20260129_33905585.html) | 读取页面只给出简称；所选方向的全称取自检索结果摘要 |
| 计划整体介绍（2022） | [基金委网站 75624](https://www.nsfc.gov.cn/p1/2859/3191/75624.html) | 用户原件所用来源 |
| 结构：国家自然科学基金申请书正文"立项依据与研究内容"的通用提纲 | 凭已有知识 | **未核实** |

**待核对**：
1. 所选方向的全称与指南原文是否逐字一致；
2. 2026 年重大研究计划重点支持项目的申请书是否沿用这份提纲（2026 年的改版明确只适用于面上项目和青年 C 类）；
3. 换一个方向是否更符合你的原意——另外 7 个方向都可以换用。

### task_012 · 非侵入式脑机接口调控言语加工（用户提供）

**原件**：依据一则科研进展报道和 PNAS 论文重构，不是申报指南。

**相对原件的改动**：
1. `task_id` 改为 task_012。
2. 原标题是一则新闻标题，改为项目标题；选题定位为在已发表发现基础上的后续项目。
3. 删去 `team` 中的真实人名，background 改为引用论文。否则生成的申请书等于冒用这位学者的身份。
4. requirements 里"重点关注左侧听觉皮层"等于直接给出了答案，改为一般表述。
5. `proposal_structure` 转成流水线使用的 `structure.core_sections`。删去"项目基本信息与总体定位"，第 7 节删去经费预算，两者都属于生成范围之外。
6. 删除 `advantages` 字段（流水线和评审都不读它）。

| 事实 | 来源 | 状态 |
|---|---|---|
| 2026 年度主责单位为基金委；执行期结束不晚于 2030 年 12 月；每个项目下设课题不超过 5 个；原则上每个方向支持 1 个项目；负责人条件；申报时间 3 月 25 日至 4 月 28 日 | [上海交大科研院转载](https://keyan.sjtu.edu.cn/ky-tongzhi/20260309/5863.html)、[科技部通知页](https://service2.most.gov.cn/kjjh_tztg_all/20260306/5810.html) | 已读 |
| 2025 年度：青年科学家项目 A 类不超过 500 万元、B 类不超过 300 万元，常规项目执行期一般为 5 年 | [中国科大转载](https://kyb.ustc.edu.cn/2025/0414/c6077a680140/page.htm) | 检索摘要，仅作参考 |
| 具体指南方向、各方向的经费上限 | 指南附件 | **需登录才能查看，未能读取** |

**待核对**：
- 2026 年指南附件里是否有对应的脑机接口或神经调控方向，以及它的经费上限和执行期。
- 结构沿用你给的结构，不是官方模板，是 12 个 task 中唯一的例外。

### task_003–006 · 补充官方结构（现有 task，2026-09-30）

原 task 都没有 `structure` 字段。修订版已冻结在本目录，`cases/tasks/` 里的原文件没有改动。

| task | 结构来源 | 来源等级 | 待核对 |
|---|---|---|---|
| 003 Wellcome | Wellcome Discovery Awards 申请表样表（2026-04-07 更新版，本地文件 `docs/_scratch/sample-full-app-form-wellcome-discovery-award.pdf`）：Research Summary（200 词）、Research Vision（3000 词，含 5 项必备内容，正文不得放图表）、Outputs Management and Sharing（500 词） | 官方原文 | 样表是否与现行申请系统中的表格一致 |
| 004 重点研发计划 | 中国农业大学科研院《申报须知及常见问题解答》（2022）列出的答辩评审要点，以及检索摘要中对正式申报书"八部分"的二手描述；研究基础、团队、经费三部分不在生成范围内 | **二手**：官方模板在国科管系统内，需登录 | 请用系统中的正式申报书模板核对章节名 |
| 005 教育部人文社科 | B 表匿名要求来自教育部社科司的常见问题释疑（2020，复旦大学转载的官方附件）；五个栏目名来自检索摘要对"课题论证"内容的二手描述 | **二手**：《申请评审书》需在教育部人文社会科学研究管理平台下载 | 请用 2025 或 2026 年版 B 表核对栏目名和字数限制 |
| 006 NSF DMREF | NSF 25-508 Section V 对 Project Description 的要求：与 DMREF 目标的联系、数据与软件基础设施、人才培养计划、以 "Management Plan" 为标题的管理计划（含分工与里程碑）、15 页上限；闭环要求来自 Section II | 官方原文 | 是否已有比 25-508 更新的 solicitation |

**task_006 附带更新**：23-530 已被 [NSF 25-508](https://nsf-gov-resources.nsf.gov/files/nsf25508.pdf) 取代，原文写明 "This document replaces Program solicitation NSF 23-530"。`program` 字段已改为 25-508；资助额度（4 年 150 万至 200 万美元）和团队至少 2 名 Senior/Key Personnel 两项没有变化。

**建议**：task_004 和 task_005 的官方模板需要登录下载。如果你或合作者手上有账号，把两份 Word 模板发过来，我按原文重新转写，这样 12 个 task 中除 task_012 外都能做到"结构来自官方原文"。

## 查询过程

- 检索：ogrants 公开项目列表；NIH Parent R21；NSF CAREER solicitation；ERC StG 2026 的 Part I/II 结构；2026 年面上项目申请书正文提纲；2026 年面上项目资助期限与强度（未找到官方原文）。
- ogrants 数据：浅克隆 `github.com/weecology/ogrants`，读取 `_grants/*.md` 的 front matter，筛选出 program 为 CAREER、R21、ERC Starting Grant 的项目，以及学科涉及计算的近年项目。
- 用户 task 的补充检索：该重大研究计划的 2026 年度项目指南；"脑科学与类脑研究"重大专项的 2025、2026 年度申报指南（具体方向需登录）。
- 用户原件没有存入仓库。脑机接口那份的来源是用户本地的 `call2(1).json`；多物理场那份是在对话中直接粘贴的。
- 没能读取的来源：UZH 的 ERC 写作指南（PDF 有密码）；北京大学的基金形式审查 PDF（TLS 证书校验失败）。
