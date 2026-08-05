"""External-knowledge retrieval for the rubric panel.

Before scoring, we extract the proposal's most check-worthy claims (novelty /
metric / method) with one LLM call, then retrieve real papers from OpenAlex
(Semantic Scholar available as an alternative). The resulting "evidence cards"
are injected into the science judge, so its `scientific_quality` / `innovation`
verdicts on over-claiming rest on real literature rather than model memory.

The retriever only supplies EVIDENCE — never a verdict. The judge still decides.
All failures degrade gracefully to an empty pack (→ pure-LLM judging).
"""
from __future__ import annotations

import json
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

MAX_PROPOSAL_CHARS = 24000

CLAIM_TYPES = ("novelty", "metric", "method")

NEUTRAL_EVIDENCE = "(未提供外部检索证据)"

# ── SSL context matching fix_references.py (some proxies break cert chains) ──
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


@dataclass
class Claim:
    claim: str
    query: str
    type: str  # novelty | metric | method


@dataclass
class EvidencePack:
    claims: List[Claim] = field(default_factory=list)
    results: Dict[int, List[dict]] = field(default_factory=dict)  # claim index → papers

    def is_empty(self) -> bool:
        return not self.claims

    def format_cards(self, types: Optional[tuple] = None) -> str:
        """Formatted evidence cards, optionally restricted to certain claim types."""
        lines: List[str] = []
        n = 0
        for i, c in enumerate(self.claims):
            if types is not None and c.type not in types:
                continue
            n += 1
            lines.append(f"【待核查论断{n}】{c.claim}")
            lines.append(f"  检索词: {c.query}")
            papers = self.results.get(i, [])
            if not papers:
                lines.append("  相关文献: 未检索到直接相关工作（可能确属新颖，也可能过于冷门）。")
            else:
                lines.append("  检索到的真实文献:")
                for p in papers:
                    meta = f"{p.get('venue') or '?'} {p.get('year') or '?'}, {p.get('authors') or '?'}"
                    cites = p.get("citations")
                    if cites is not None:
                        meta += f"; 被引{cites}"
                    lines.append(f"    - {p.get('title','')} ({meta})")
                    abs = (p.get("abstract") or "").strip()
                    if abs:
                        lines.append(f"      摘要节选: {abs[:200]}")
            lines.append("")
        if not lines:
            return NEUTRAL_EVIDENCE
        header = "针对以下待核查论断，系统检索到的真实文献证据（仅供参考，判断权在你，勿被检索结果直接左右）：\n"
        return header + "\n".join(lines)


# ═══════════════════════════════ claim extraction ═══════════════════════════════

EXTRACT_SYSTEM = """你是科研评审助手。你的任务是从一份基金申请书中挑出最需要用外部文献核查的论断，供后续检索真实论文。只输出 JSON，不要 markdown。"""

EXTRACT_USER = """从下面的申请书中挑出至多 ${max_claims} 条最该核查的论断，分三类：
- novelty：宣称新颖性的论断（如"首次""填补空白""提出X新机制/新范式"）
- metric：具体量化指标（如"成功率≥85%""亲和力提升10倍""误差≤1.0"）
- method：依赖的关键已有方法，需核对其真实能力（如 GCG、PAIR、RFdiffusion、AlphaFold 等）

对每条论断给出一个**英文**检索词（query），用于在 Semantic Scholar 检索相关论文。
优先挑选"若不成立则严重影响评审结论"的论断。

## 申请书
${proposal_text}

只输出：
{
  "claims": [
    {"claim": "论断原文或简述", "query": "english search terms", "type": "novelty|metric|method"}
  ]
}"""


def _parse_json(text: str) -> Dict[str, Any]:
    try:
        s = text.find("{")
        e = text.rfind("}") + 1
        if s >= 0 and e > s:
            return json.loads(text[s:e])
    except Exception:
        pass
    return {}


def extract_claims(llm: Any, proposal_text: str, max_claims: int = 6) -> List[Claim]:
    """One LLM call → list of check-worthy claims. Returns [] on any failure."""
    if len(proposal_text) > MAX_PROPOSAL_CHARS:
        proposal_text = proposal_text[:MAX_PROPOSAL_CHARS]
    user = (EXTRACT_USER
            .replace("${max_claims}", str(max_claims))
            .replace("${proposal_text}", proposal_text))
    try:
        raw = llm.generate_text(system_prompt=EXTRACT_SYSTEM, user_prompt=user)
    except Exception:
        return []
    data = _parse_json(raw)
    claims: List[Claim] = []
    for item in (data.get("claims", []) if isinstance(data, dict) else [])[:max_claims]:
        if not isinstance(item, dict):
            continue
        claim = str(item.get("claim", "")).strip()
        query = str(item.get("query", "")).strip()
        ctype = str(item.get("type", "method")).strip().lower()
        if claim and query:
            claims.append(Claim(claim=claim, query=query, type=ctype))
    return claims


# ═══════════════════════════════ retrieval ═══════════════════════════════

def _reconstruct_abstract(inverted: Optional[dict]) -> str:
    """OpenAlex returns abstracts as an inverted index {word: [positions]}."""
    if not inverted:
        return ""
    positions: List[tuple] = []
    for word, idxs in inverted.items():
        for i in idxs:
            positions.append((i, word))
    positions.sort()
    return " ".join(w for _, w in positions)


def search_openalex(query: str, limit: int = 3, max_retries: int = 3) -> List[dict]:
    """Search OpenAlex for real papers (free, no API key). Returns [] on failure.

    Preferred default: no key required and generous rate limits. Set
    AI4PROPOSAL_MAILTO=you@example.com to join the faster "polite pool".
    """
    mailto = os.getenv("AI4PROPOSAL_MAILTO", "ai4proposal@example.com").strip()
    encoded = urllib.parse.quote(query)
    url = (
        "https://api.openalex.org/works"
        f"?search={encoded}&per-page={limit}"
        "&select=title,publication_year,cited_by_count,primary_location,authorships,abstract_inverted_index"
        f"&mailto={urllib.parse.quote(mailto)}"
    )
    headers = {"User-Agent": f"AI4Proposal/1.0 (mailto:{mailto})"}

    data = None
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=20, context=_SSL_CTX) as resp:
                data = json.loads(resp.read().decode())
            break
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < max_retries - 1:
                time.sleep(2 ** attempt * 2)  # 2s, 4s, 8s
                continue
            return []
        except Exception:
            return []
    if data is None:
        return []

    papers = []
    for w in data.get("results", []) or []:
        source = (w.get("primary_location") or {}).get("source") or {}
        authors = [
            (a.get("author") or {}).get("display_name", "")
            for a in (w.get("authorships") or [])[:3]
        ]
        papers.append({
            "title": w.get("title", "") or "",
            "authors": ", ".join(a for a in authors if a),
            "year": w.get("publication_year"),
            "venue": source.get("display_name") or "",
            "abstract": _reconstruct_abstract(w.get("abstract_inverted_index")),
            "citations": w.get("cited_by_count"),
        })
    return papers


def search_semantic_scholar(query: str, limit: int = 3, max_retries: int = 3) -> List[dict]:
    """Search Semantic Scholar for real papers. Returns [] on any failure.

    The free anonymous endpoint is heavily rate-limited (HTTP 429). Set
    AI4PROPOSAL_S2_API_KEY (free from semanticscholar.org) for a higher quota;
    otherwise we back off and retry on 429.
    """
    encoded = urllib.parse.quote(query)
    url = (
        "https://api.semanticscholar.org/graph/v1/paper/search"
        f"?query={encoded}&limit={limit}"
        "&fields=title,authors,year,venue,abstract,citationCount"
    )
    headers = {"User-Agent": "AI4Proposal/1.0"}
    api_key = os.getenv("AI4PROPOSAL_S2_API_KEY") or os.getenv("SEMANTIC_SCHOLAR_API_KEY")
    if api_key:
        headers["x-api-key"] = api_key.strip()

    data = None
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=20, context=_SSL_CTX) as resp:
                data = json.loads(resp.read().decode())
            break
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < max_retries - 1:
                time.sleep(2 ** attempt * 3)  # 3s, 6s, 12s
                continue
            return []
        except Exception:
            return []
    if data is None:
        return []
    papers = []
    for p in data.get("data", []) or []:
        authors = [a.get("name", "") for a in (p.get("authors") or [])[:3]]
        papers.append({
            "title": p.get("title", ""),
            "authors": ", ".join(a for a in authors if a),
            "year": p.get("year"),
            "venue": p.get("venue") or "",
            "abstract": p.get("abstract") or "",
            "citations": p.get("citationCount"),
        })
    return papers


# ═══════════════════════════════ orchestration ═══════════════════════════════

def gather_evidence(
    llm: Any,
    proposal_text: str,
    search_fn: Callable[[str, int], List[dict]] = search_openalex,
    max_claims: int = 6,
    per_query: int = 3,
    delay: float = 1.0,
    verbose: bool = False,
) -> EvidencePack:
    """Extract claims + retrieve evidence. Never raises; degrades to empty pack."""
    claims = extract_claims(llm, proposal_text, max_claims=max_claims)
    if verbose:
        print(f"    [evidence] extracted {len(claims)} claims")
    results: Dict[int, List[dict]] = {}
    for i, c in enumerate(claims):
        try:
            papers = search_fn(c.query, per_query)
        except Exception:
            papers = []
        results[i] = papers
        if verbose:
            print(f"    [evidence] '{c.query[:50]}' → {len(papers)} papers")
        if delay and i < len(claims) - 1:
            time.sleep(delay)  # S2 free tier: ~1 req / 3s
    return EvidencePack(claims=claims, results=results)
