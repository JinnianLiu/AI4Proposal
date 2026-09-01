#!/usr/bin/env python3
"""Replace all hallucinated references with real ones from Semantic Scholar API."""
import json, time, urllib.request, urllib.parse, ssl
from pathlib import Path

ctx = ssl.create_default_context()
ctx.check_hostname = False; ctx.verify_mode = False

# Domain → search queries for Semantic Scholar
DOMAIN_QUERIES = {
    "ai_safety": [
        "adversarial attacks large language models jailbreak",
        "RLHF safety alignment language models",
        "red teaming LLM automated security evaluation",
        "multimodal safety vision language models",
        "AI constitutional alignment value pluralism",
    ],
    "agentic_ai": [
        "LLM agents autonomous task planning",
        "multi-agent coordination language models",
        "tool-augmented language agents API",
        "code generation agents verification",
        "GUI agents vision language grounding",
    ],
    "multimodal": [
        "vision language model multimodal understanding",
        "video understanding temporal reasoning transformer",
        "medical multimodal foundation model CT MRI",
        "audio visual scene understanding sound source",
    ],
    "efficient_ml": [
        "efficient LLM inference quantization pruning",
        "green AI carbon efficient training",
        "mixture of experts sparse activation routing",
        "tinyML on-device learning compression",
    ],
    "ai4science": [
        "AI protein design diffusion model structure prediction",
        "neural network potential molecular dynamics universal",
        "AI weather climate prediction physics-informed",
        "AI materials discovery active learning DFT",
        "AI drug discovery molecular generation ADMET",
        "neural PDE solver operator learning fluid dynamics",
    ],
    "robotics": [
        "robot manipulation dexterous hand tactile sensing",
        "foundation model robot learning cross embodiment",
        "humanoid robot locomotion whole body control",
        "multi-robot coordination distributed control",
        "sim-to-real transfer visuomotor policy",
    ],
    "privacy_security": [
        "differential privacy foundation model DP-SGD",
        "machine unlearning data privacy right to be forgotten",
        "model watermarking copyright protection backdoor",
        "adversarial robustness vision language multimodal",
        "deepfake detection AIGC content provenance",
    ],
    "nlp": [
        "LLM hallucination factuality faithful generation",
        "long context language model efficient attention",
        "multilingual language model fairness low resource",
        "chain of thought reasoning faithfulness verification",
    ],
    "vision": [
        "open world visual recognition continual learning",
        "3D reconstruction sparse view gaussian splatting NeRF",
        "autonomous driving perception BEV end-to-end",
        "remote sensing image interpretation change detection",
    ],
    "default": [
        "foundation model artificial intelligence machine learning",
        "deep learning transformer attention neural network",
    ],
}


def search_semantic_scholar(query: str, limit: int = 5) -> list:
    """Search Semantic Scholar for real papers."""
    encoded = urllib.parse.quote(query)
    url = f"https://api.semanticscholar.org/graph/v1/paper/search?query={encoded}&limit={limit}&fields=title,authors,year,venue,externalIds"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "AI4Proposal/1.0"})
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            data = json.loads(resp.read().decode())
        papers = []
        for p in data.get("data", []):
            authors = [a.get("name", "") for a in p.get("authors", [])[:3]]
            papers.append({
                "title": p.get("title", ""),
                "authors": ", ".join(authors) if authors else "",
                "year": p.get("year", 0),
                "venue": p.get("venue", "") or "",
                "paper_id": p.get("paperId", ""),
            })
        return papers
    except Exception as e:
        print(f"    S2 API error: {e}")
        return []


def main():
    topics_dir = Path("legacy/research_topics")
    fixed = 0

    for fpath in sorted(topics_dir.glob("*.json")):
        topic = json.loads(fpath.read_text())
        domain = topic.get("domain", "default")
        queries = DOMAIN_QUERIES.get(domain, DOMAIN_QUERIES["default"])

        real_refs = []
        for q in queries[:2]:  # 2 queries per topic
            if len(real_refs) >= 5:
                break
            papers = search_semantic_scholar(q, limit=3)
            for p in papers:
                if p["title"] not in [r["title"] for r in real_refs]:
                    real_refs.append(p)
            time.sleep(3.0)  # Rate limit: 1 call per 3s to avoid 429

        if real_refs:
            topic["references"] = real_refs[:6]
            json.dump(topic, fpath.open("w"), indent=2, ensure_ascii=False)
            fixed += 1
            print(f"  ✓ {fpath.stem}: [{domain}] {len(real_refs)} real refs")
        else:
            print(f"  ✗ {fpath.stem}: [{domain}] no refs found")

    print(f"\nFixed {fixed} topics with real Semantic Scholar references")


if __name__ == "__main__":
    main()
