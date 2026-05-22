# AI4Proposal

**Multi-Agent Grant Proposal Generation System with Built-in Benchmark & AI Judge.**

Given a research topic and open challenges, AI4Proposal automatically generates a complete, submission-ready grant proposal (基金本子) — 10 sections, 10,000+ Chinese characters, with AI-generated figures and multi-dimensional automated scoring.

```
Research Topic → [Writer → Reviewer → Reviser]×10 sections → Figure Agent → AI Judge → .docx
```

## Highlights

- **V4 Pipeline**: Step-by-step section generation with iterative review loop — no timeout, full 10-section output
- **88-Topic Benchmark**: 24 AI sub-fields, Chinese/English bilingual, each with detailed background, open challenges, and references
- **AI Judge**: 7-dimension LLM-based scoring with strengths, weaknesses, and verdict
- **Auto Figures**: GPT-Image-2 integration for technical diagrams embedded in final .docx
- **Model Agnostic**: Works with Claude, GPT, Qwen, DeepSeek — any OpenAI-compatible API

## Quickstart

### 1. Setup

```bash
git clone https://github.com/your-org/ai4proposal
cd ai4proposal
pip install -e .
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env with your API keys
```

Required environment variables:
```bash
export AI4PROPOSAL_API_KEY=sk-your-key
export AI4PROPOSAL_BASE_URL=https://api.openai.com/v1
export AI4PROPOSAL_MODEL=gpt-4.1
```

### 3. Run

```bash
# Generate a proposal from a benchmark topic
python scripts/run_pipeline.py --topic-id topic_003

# With custom team
python scripts/run_pipeline.py --topic-id topic_021 \
    --team my_team.json \
    --output runs/my_proposal

# Without review loop (single-pass generation)
python scripts/run_pipeline.py --topic-id topic_003 --no-review
```

## Pipeline Architecture

```
┌──────────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
│  Research    │───▶│  Writer  │───▶│ Reviewer │───▶│ Reviser  │───▶│ Assembler│
│  Topic       │    │ (10 sections) │ (score 1-10) │ (if < 7)   │    │          │
└──────────────┘    └──────────┘    └──────────┘    └──────────┘    └──────────┘
                                                                          │
┌──────────┐    ┌──────────┐                                           │
│  .docx   │◀───│ Figure   │◀──────────────────────────────────────────┘
│  output  │    │ Agent    │
└──────────┘    └──────────┘
```

### V4 Pipeline (current)

Each of the 10 proposal sections is generated independently:
1. **Writer** (LLM) generates one section (300-600 words)
2. **Reviewer** (LLM) scores it 1-10 with strengths/weaknesses/suggestions
3. **Reviser** (LLM) revises the section if score < 7 (up to 2 rounds)
4. **Assembler** merges all 10 sections into a complete proposal
5. **Figure Agent** scans `[figure: description]` markers → calls GPT-Image-2 → embeds in .docx
6. **AI Judge** holistically scores the final proposal on 7 dimensions

This step-by-step approach avoids API timeouts that plague end-to-end generation.

### AI Judge Dimensions

| Dimension | Description |
|-----------|-------------|
| `scientific_quality` | Scientific/technical excellence and rigour |
| `feasibility` | Realistic approach and well-planned execution |
| `innovation` | Novelty and creative thinking |
| `clarity` | Writing quality, structure, readability |
| `compliance` | Section completeness and constraint satisfaction |
| `impact` | Potential significance and broader impact |
| `team_fit` | Team expertise match with the research topic |

Output includes: numerical scores, 4-5 specific strengths, 4-5 specific weaknesses, overall verdict (`recommend_submit` / `revise_resubmit` / `reject`), and a one-paragraph summary.

## Benchmark

### 88 Research Topics × 24 AI Sub-fields

```
AI Safety (4)   Agentic AI (5)   Multimodal (5)   Efficient ML (5)
AI4Science (6)  Robotics (5)     Privacy (5)      NLP (4)
Vision (5)      AI Infra (5)     Embodied AI (4)  Healthcare (5)
ML Theory (3)   Climate AI (4)   AI Education (2) AI4Code (3)
AI Society (2)  AI Hardware (3)  AI Finance (2)   Agriculture (2)
AI Energy (2)   Neuroscience (3) AI4Math (2)      AI Networks (2)
```

Each topic contains:
- **Title**: Research direction
- **Background**: 800-2,000 characters with [1][2][3] citations to prior work
- **Challenges**: 4-5 specific open problems
- **References**: 6-7 papers with venue and year
- **Sponsor**: NSFC / NSF / Horizon Europe with budget amount
- **Language**: Chinese (46) / English (42)

### Topic Format

```json
{
  "topic_id": "topic_003",
  "domain": "ai_safety",
  "language": "zh",
  "title": "面向大语言模型的自动化红队测试与多维安全评估框架研究",
  "sponsor": "国家自然科学基金",
  "budget": {"amount": 2800000, "currency": "CNY"},
  "background": "大语言模型在对抗性提示、间接注入与长上下文操纵下的安全脆弱性已成为...",
  "challenges": [
    "对抗性提示的语义自然性、多样性与跨模型迁移性难以兼顾",
    "多维安全评测基准的动态构建与抗污染机制缺失",
    "攻防协同闭环框架的稳定性、收敛性与算力效率瓶颈"
  ],
  "references": [
    {"title": "Universal and Transferable Adversarial Attacks...", "venue": "ICML 2024", "authors": "Zou et al."}
  ]
}
```

### Extending the Benchmark

```bash
# Generate more topics (requires LLM API)
python build_topics.py
```

Edit `TOPIC_SEEDS` in `build_topics.py` to add new research areas.

## Generated Output

Each run produces:

```
runs/<case_id>/
├── proposal_final.md          # Complete proposal (10 sections, Markdown)
├── score.json                 # AI Judge scores + strengths + weaknesses
├── fig_01.png                 # Generated technical diagram
├── fig_02.png                 # Generated roadmap
├── fig_03.png                 # Generated Gantt chart
├── figure_manifest.json       # Figure descriptions
└── sec_*.md                   # Individual section files
```

### Sample Cherry-Pick Results

| Case | Domain | Words | Sections | Figures | AI Judge | Verdict |
|------|--------|-------|----------|---------|----------|---------|
| AI Safety | ai_safety | 11,400 | 10/10 | 3 | 80/100 | recommend_submit |
| Protein Design | ai4science | 14,400 | 10/10 | 3 | 80/100 | recommend_submit |

## Project Structure

```
ai4proposal/
├── .env.example               # Configuration template
├── .gitignore
├── pyproject.toml
├── README.md
│
├── src/ai4proposal/           # Core library
│   ├── config.py              #   Env var configuration (no hardcoded keys)
│   ├── llm.py                 #   LLM backend (OpenAI-compatible API)
│   ├── ai_judge.py            #   7-dimension AI proposal scoring
│   ├── pipeline_v4.py         #   V4: Step-by-step generation + review loop
│   ├── eval_v2.py             #   Evaluation orchestration
│   └── pipeline_v3.py         #   V3: End-to-end generation (legacy)
│
├── build_topics.py            # Benchmark: generate research topics via LLM
│
├── scripts/
│   └── run_pipeline.py        # CLI: run pipeline on a topic
│
├── cases/
│   └── research_topics/       # 88 benchmark topics
│       ├── topic_001.json
│       ├── topic_002.json
│       └── ...
│
└── tests/
```

## Configuration

All configuration via environment variables. No keys in source code.

| Variable | Required | Default | Description |
|----------|:--------:|---------|-------------|
| `AI4PROPOSAL_API_KEY` | Yes | — | LLM API key |
| `AI4PROPOSAL_BASE_URL` | No | `api.openai.com/v1` | LLM API endpoint |
| `AI4PROPOSAL_MODEL` | No | `gpt-4.1` | Model name |
| `AI4PROPOSAL_USE_REVIEW` | No | `1` | Enable review loop (0/1) |
| `AI4PROPOSAL_MAX_REVISIONS` | No | `2` | Max revision rounds |
| `AI4PROPOSAL_IMAGE_API_KEY` | No | Same as LLM | Image gen API key |
| `AI4PROPOSAL_IMAGE_MODEL` | No | `gpt-image-2` | Image model |

## Supported Models

Any OpenAI-compatible API works. Tested with:

| Model | API Provider | Notes |
|-------|-------------|-------|
| Claude Opus 4.7 | micuapi.ai (proxy) | Best quality, 120s timeout per call |
| GPT-4.1 | ChatAnywhere / OpenAI | Good quality, stable |
| GPT-4o | OpenAI / ChatAnywhere | Fast, good for Reviewer |
| GLM-5.1 | mydamoxing.cn | Chinese-optimized |
| DeepSeek-V3 | DeepSeek API | Open-source alternative |
| Qwen-3 | Alibaba / local | Open-source, requires deployment |

## Contributing

Areas open for contribution:

- **New Topics**: Add research seeds to `build_topics.py` for under-represented domains
- **New Sponsors**: Add templates for ERC, JST, DFG, CIHR, etc.
- **Open-Source Models**: Test and optimize prompts for Qwen, DeepSeek, Llama
- **Vector Figures**: Replace raster GPT-Image-2 with Mermaid/PlantUML SVG generation
- **Human Evaluation**: Build blind review interface for comparing generated vs. human proposals
- **Multi-Language**: Extend beyond Chinese/English to Japanese, Korean, German, etc.
- **Prompt Optimization**: Improve Writer/Reviewer prompts for higher quality output

### Adding a New Model

```python
# In scripts/run_pipeline.py or your own script:
from ai4proposal.pipeline_v4 import PipelineConfig

config = PipelineConfig(
    model="your-model-name",
    api_key=os.environ["YOUR_API_KEY"],
    base_url="https://your-api-endpoint.com/v1",
)
```

## License

MIT

## Citation

If you use AI4Proposal in your research, please cite:

```bibtex
@software{ai4proposal2026,
  title = {AI4Proposal: Multi-Agent Grant Proposal Generation System},
  year = {2026},
  url = {https://github.com/your-org/ai4proposal}
}
```
