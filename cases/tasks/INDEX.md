# Guideline-Grounded Task Pack

基于**真实公开资助指南**转写的初始 task（跨学科），作为后续 evaluation / pipeline / 大规模 benchmark 的种子。

与 `cases/research_topics/topic_*.json`（纯合成学术选题）的区别：每个 task 带有真实指南的**硬约束** —— 经费上限 (`budget.is_cap`)、资格 (`eligibility`)、硬交付物 (`requirements`)、强制约束 (`constraints`)、以及溯源 (`provenance`)。

## Schema 新增字段

| 字段 | 含义 |
|------|------|
| `program` | 真实资助项目/指南名称 |
| `direction` | 指南多方向时锁定的具体方向 |
| `budget.is_cap` | `true`=经费为上限；`false`=概算/无固定上限 |
| `eligibility` | 申请资格（职称/年龄/单位/地域等） |
| `requirements` | 硬交付物清单（专利/开源/论文/成果形式/考核指标） |
| `constraints` | 硬约束（周期/预算/强制技术/方向限定） |
| `provenance.origin_type` | `public_guideline` / `public_guideline_plus_expert_reconstruction` / `bootstrap_synthetic` |
| `provenance.basis_urls` | 官方/一手来源链接 |
| `provenance.note` | 哪些是真实转写、哪些是重构 |

## 任务清单

| task | 领域 | 语言 | 资助方 / 项目 | 经费 | origin | 来源 |
|------|------|------|--------------|------|--------|------|
| [task_001](task_001.json) | AI/系统软件 | zh | 众智FlagOS加速计划—智源学者（方向6） | ≤200万 CNY / 1年 | guideline+recon | 用户提供原文 |
| [task_002](task_002.json) | 人文（历史/文献学） | zh | 国家社科基金重大项目（2024公开招标） | 60–80万 CNY / 3–5年 | guideline+recon | [nopss](http://www.nopss.gov.cn/n1/2024/0412/c431028-40214823.html) |
| [task_003](task_003.json) | 生物医学/健康 | en | Wellcome Discovery Awards | ~£3.5M avg / 3–8yr | guideline+recon | [wellcome.org](https://wellcome.org/research-funding/schemes/wellcome-discovery-awards) |
| [task_004](task_004.json) | 合成生物学 | zh | 国家重点研发计划"合成生物学"重点专项（2024） | 概算（应用研究，1:1配套）/ ≤5年 | guideline+recon | [MOST/NSFC 指南 PDF](https://irace.hkbu.edu.hk/content/dam/irace-assets/research/document/0.%20Call%20for%20applications_2024%20MOST_%E5%90%88%E6%88%90%E7%94%9F%E7%89%A9%E5%AD%A6%E9%87%8D%E7%82%B9%E4%B8%93%E9%A1%B92024%E5%B9%B4%E5%BA%A6%E9%A1%B9%E7%9B%AE.pdf) |
| [task_005](task_005.json) | 社会科学（管理/经济） | zh | 教育部人文社会科学研究一般项目（2024规划基金） | ≤10万 CNY / 3年 | guideline+recon | [moe.gov.cn](https://hudong.moe.gov.cn/s78/A13/tongzhi/202403/t20240319_1121202.html) |
| [task_006](task_006.json) | 材料科学/物理 | en | NSF DMREF（NSF 23-530） | $1.5–2M / 4年 | guideline+recon | [nsf.gov](https://www.nsf.gov/funding/opportunities/dmref-designing-materials-revolutionize-engineer-our-future/nsf23-530/solicitation) |

## 溯源说明

全部 6 个 task 的 `origin_type` 均为 `public_guideline_plus_expert_reconstruction`：

- **真实转写部分**：资助方、项目名、经费额度/上限、研究周期、申请资格、交付物/成果形式、强制约束、方向划分 —— 均来自 `basis_urls` 官方指南原文。
- **合理重构部分**：具体研究 `title` / `background` / `challenges` —— 这些指南本身不指定具体课题（由申请人自拟），故为在该方向下的合理选题重构。
- **待补/占位**：`references` 全部留空，待后续用真实文献补充；task_004 的量化考核指标与经费金额因官方 PDF 未能解析为占位重构值，以官方指南为准（见其 `provenance.note`）。
- **例外说明**：task_003 官方页面直接抓取返回 HTTP 403，事实取自其官方 scheme 摘要与佐证来源。
