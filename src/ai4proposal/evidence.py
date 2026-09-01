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
import re
import ssl
import time
from math import ceil
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from xml.etree import ElementTree
from typing import Any, Callable, Dict, List, Optional

from .llm import parse_json

MAX_PROPOSAL_CHARS = 24000

CLAIM_TYPES = ("novelty", "metric", "method")

NEUTRAL_EVIDENCE = "(未提供外部检索证据)"

# ── TLS: verify by default, fall back only when the chain genuinely fails ──
# Some corporate proxies / VPNs terminate TLS with their own CA, which breaks
# verification. Rather than disabling checks outright, try a verified connection
# first and drop to an unverified one only on an SSL error. Set
# AI4PROPOSAL_INSECURE_TLS=1 to skip straight to unverified.
_SSL_VERIFIED = ssl.create_default_context()

_SSL_UNVERIFIED = ssl.create_default_context()
_SSL_UNVERIFIED.check_hostname = False
_SSL_UNVERIFIED.verify_mode = ssl.CERT_NONE

_warned_insecure = False


def _insecure_allowed() -> bool:
    return os.getenv("AI4PROPOSAL_INSECURE_TLS", "").strip().lower() in {"1", "true", "yes", "on"}


def _urlopen(req: urllib.request.Request, timeout: int = 20):
    """Open `req` with certificate verification, retrying unverified if the TLS
    chain fails. Only SSL errors trigger the fallback — every other error
    propagates, so a 429 or timeout is still handled by the caller's retry loop."""
    global _warned_insecure
    if not _insecure_allowed():
        try:
            return urllib.request.urlopen(req, timeout=timeout, context=_SSL_VERIFIED)
        except ssl.SSLError:
            if not _warned_insecure:
                print("    [evidence] TLS 证书校验失败（可能是代理拆包），本次改用不校验连接；"
                      "检索到的文献无法保证来源真实")
                _warned_insecure = True
    return urllib.request.urlopen(req, timeout=timeout, context=_SSL_UNVERIFIED)


@dataclass
class Claim:
    claim: str
    queries: List[str]      # several angles per claim; results are unioned
    type: str               # novelty | metric | method

    @property
    def query(self) -> str:
        """Primary query, for logging."""
        return self.queries[0] if self.queries else ""


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
                lines.append("  相关文献: 未检索到直接相关工作。注意这**不能**作为该论断不成立的证据——"
                             "文献库对近 1-2 年成果、新模型名称与预印本收录滞后，冷门方向亦可能查无结果。")
            else:
                lines.append("  关键词匹配到的文献（未经人工筛选，可能与论断无关）:")
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
        header = (
            "以下为系统按关键词自动检索的文献，**未经人工筛选**，其中可能混有与论断无关的结果。\n"
            "使用方式：先自行判断每篇是否真的与论断相关，只采信相关者；不相关的直接忽略，"
            "不要因为列出了文献就认为论断已被证实或证伪。判断权在你。\n"
        )
        return header + "\n".join(lines)


# ═══════════════════════════════ claim extraction ═══════════════════════════════

EXTRACT_SYSTEM = """你是科研评审助手。你的任务是从一份基金申请书中挑出最需要用外部文献核查的论断，供后续检索真实论文。只输出 JSON，不要 markdown。"""

EXTRACT_USER = """从下面的申请书中挑出至多 ${max_claims} 条最该核查的论断，分三类：
- novelty：宣称新颖性的论断（如"首次""填补空白""提出X新机制/新范式"）
- metric：具体量化指标（如"成功率≥85%""亲和力提升10倍""误差≤1.0"）
- method：依赖的关键已有方法，需核对其真实能力（如 GCG、PAIR、RFdiffusion、AlphaFold 等）

优先挑选"若不成立则严重影响评审结论"的论断。

对每条论断给出一个**英文检索词**（query）。检索走的是学术文献库的关键词匹配，**不是搜索引擎**，
写法直接决定检索质量：

- 只写 **3-6 个精确的技术名词**，用空格分隔。例如 `paged attention KV cache LLM serving`。
- **不要写整句英文**。像 `high visual quality does not equal task success` 这样的句子，
  会因为 quality / task / success 等高频词匹配到大量完全无关的高引论文。
- **不要用引号、OR、AND、括号**等语法，文献库不支持，只会被当成普通字符。
- **不要只用通用词**（system / method / model / framework / performance / evaluation / data /
  learning / task / world / real-time）。这类词必须与具体的技术名词搭配出现。
- 优先使用**该领域的专有名称**：方法名、模型名、数据集名、基准名、算法名、架构名。
- 若论断涉及具体数值指标，检索该指标背后的**技术手段**，而不是数字本身
  （`40% communication latency hiding` 应写成 `communication computation overlap distributed training`）。

每条论断给 **2-3 个不同角度**的检索词，结果取并集，以提高召回：
一个用具体的方法/模型/系统专有名称；一个用该问题的通用技术表述；必要时再加一个相邻技术族的说法。

## 申请书
${proposal_text}

只输出：
{
  "claims": [
    {"claim": "论断原文或简述",
     "queries": ["specific method name terms", "general problem terms", "adjacent technique terms"],
     "type": "novelty|metric|method"}
  ]
}"""


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
    data = parse_json(raw)
    claims: List[Claim] = []
    for item in (data.get("claims", []) if isinstance(data, dict) else [])[:max_claims]:
        if not isinstance(item, dict):
            continue
        claim = str(item.get("claim", "")).strip()
        raw_qs = item.get("queries")
        if isinstance(raw_qs, str):
            raw_qs = [raw_qs]
        elif not isinstance(raw_qs, list):
            raw_qs = [item.get("query", "")]      # tolerate the older single-query shape
        queries = [str(q).strip() for q in raw_qs if str(q).strip()][:3]
        ctype = str(item.get("type", "method")).strip().lower()
        if claim and queries:
            claims.append(Claim(claim=claim, queries=queries, type=ctype))
    return claims


# ═══════════════════════════ relevance filtering ═══════════════════════════
# OpenAlex ranks by a relevance_score that tracks citation count and term
# frequency, so it happily returns a 19k-citation survey for a query it barely
# matches — measured: a junk query outscored a precise one. Thresholding that
# score therefore does not work; requiring the query's own terms to actually
# appear in the paper does.

_QUERY_STOPWORDS = {
    "a", "an", "the", "of", "for", "and", "or", "in", "on", "with", "to", "is",
    "are", "be", "that", "this", "by", "as", "at", "from", "does", "not", "than",
    "versus", "vs", "using", "based", "via", "new", "novel", "high", "low", "its",
}


def _query_terms(query: str) -> List[str]:
    """Distinctive lowercase terms of a query, in order, de-duplicated."""
    words = re.findall(r"[a-z0-9][a-z0-9\-]*", query.lower())
    seen, out = set(), []
    for w in words:
        if len(w) > 2 and w not in _QUERY_STOPWORDS and w not in seen:
            seen.add(w)
            out.append(w)
    return out


MAX_TERMS_REQUIRED = 3


def _is_relevant(paper: dict, terms: List[str], min_fraction: float = 0.5) -> bool:
    """Keep a paper only if enough of the query's terms appear in its text.

    Guards against the failure mode where a broad query pulls in highly-cited
    papers sharing only a common word ("task", "quality", "world").

    The requirement is capped at MAX_TERMS_REQUIRED rather than scaling with
    query length: a genuinely relevant paper rarely echoes every term of a long
    query, so a strict fraction discards real hits (measured: it dropped Imagen
    Video for a query about video-diffusion latency).
    """
    if not terms:
        return True
    hay = f"{paper.get('title') or ''} {paper.get('abstract') or ''}".lower()
    hits = sum(1 for t in terms if t in hay)
    need = min(len(terms), MAX_TERMS_REQUIRED, max(2, ceil(len(terms) * min_fraction)))
    return hits >= need


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
            with _urlopen(req) as resp:
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


_ATOM = "{http://www.w3.org/2005/Atom}"


def search_arxiv(query: str, limit: int = 3, max_retries: int = 2) -> List[dict]:
    """Search arXiv. Free, no API key. Returns [] on failure.

    Complements OpenAlex, which indexes published records and so lags one to two
    years behind — exactly where novelty claims live. New model and system names
    are usually on arXiv the day they appear.
    """
    terms = " AND ".join(f'all:"{t}"' if " " in t else f"all:{t}"
                         for t in _query_terms(query)[:6]) or f"all:{query}"
    url = ("http://export.arxiv.org/api/query"
           f"?search_query={urllib.parse.quote(terms)}"
           f"&max_results={limit}&sortBy=relevance")
    headers = {"User-Agent": "AI4Proposal/1.0"}

    body = None
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with _urlopen(req, timeout=25) as resp:
                body = resp.read().decode("utf-8", errors="ignore")
            break
        except Exception:
            if attempt < max_retries - 1:
                time.sleep(2 * (attempt + 1))
            else:
                return []
    if not body:
        return []

    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError:
        return []

    papers = []
    for entry in root.findall(f"{_ATOM}entry"):
        def txt(tag: str) -> str:
            el = entry.find(f"{_ATOM}{tag}")
            return " ".join((el.text or "").split()) if el is not None else ""
        authors = [" ".join((a.findtext(f"{_ATOM}name") or "").split())
                   for a in entry.findall(f"{_ATOM}author")[:3]]
        published = txt("published")
        papers.append({
            "title": txt("title"),
            "authors": ", ".join(a for a in authors if a),
            "year": int(published[:4]) if published[:4].isdigit() else None,
            "venue": "arXiv",
            "abstract": txt("summary"),
            "citations": None,
        })
    return papers


def search_all(query: str, limit: int = 3) -> List[dict]:
    """Default retriever: OpenAlex (published record, citation counts) plus arXiv
    (preprints and recent work). Either failing degrades to the other."""
    papers = search_openalex(query, limit)
    papers.extend(search_arxiv(query, limit))
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
            with _urlopen(req) as resp:
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


# ═══════════════════════════════ reranking ═══════════════════════════════

RERANK_SYSTEM = """你是文献筛选助手。给你一条待核查论断和若干篇候选论文，你判断每篇是否**真的**与核查该论断相关。只输出 JSON。"""

RERANK_USER = """## 待核查论断
${claim}

## 候选论文
${papers}

## 判断标准
保留的条件是：这篇论文能够为"该论断是否成立/是否新颖/该指标是否可达"提供参考——
即它研究的是同一个问题、同一类方法，或给出了可比的数值结果。

以下情况一律剔除：
- 只是碰巧共用了通用词（如 world / task / quality / system / real-time / video），主题实际无关
- 属于完全不同的学科或应用领域
- 过于宽泛的综述或教科书式文献，对具体论断没有判别力

宁可少留，不要凑数。全部不相关就返回空数组。

只输出：{"keep": [1, 3]}   // 保留论文的编号"""


def rerank_papers(llm: Any, claim: str, papers: List[dict]) -> List[dict]:
    """Keep only the papers an LLM judges genuinely relevant to `claim`.

    Replaces term-overlap filtering, which cannot tell a shared common word from
    a shared topic: it discarded a real hit (Imagen Video) while keeping an
    unrelated quadrotor paper. Returns `papers` unchanged on any failure, so a
    rerank outage degrades to no filtering rather than to no evidence.
    """
    if not papers:
        return papers
    listing = []
    for i, p in enumerate(papers, 1):
        abstract = (p.get("abstract") or "").strip()[:300]
        listing.append(f"{i}. {p.get('title','')} ({p.get('venue') or '?'} {p.get('year') or '?'})"
                       + (f"\n   摘要: {abstract}" if abstract else ""))
    user = (RERANK_USER.replace("${claim}", claim)
                       .replace("${papers}", "\n".join(listing)))
    try:
        raw = llm.generate_text(system_prompt=RERANK_SYSTEM, user_prompt=user)
    except Exception:
        return papers
    data = parse_json(raw)
    keep = data.get("keep")
    if not isinstance(keep, list):
        return papers
    idx = {int(k) for k in keep if isinstance(k, (int, float, str)) and str(k).strip().isdigit()}
    return [p for i, p in enumerate(papers, 1) if i in idx]


def _dedupe(papers: List[dict]) -> List[dict]:
    """Collapse the same work retrieved by several queries or both sources."""
    seen, out = set(), []
    for p in papers:
        key = re.sub(r"[^a-z0-9]", "", (p.get("title") or "").lower())[:60]
        if key and key not in seen:
            seen.add(key)
            out.append(p)
    return out


# ═══════════════════════════════ orchestration ═══════════════════════════════

def gather_evidence(
    llm: Any,
    proposal_text: str,
    search_fn: Callable[[str, int], List[dict]] = search_all,
    max_claims: int = 6,
    per_query: int = 3,
    delay: float = 1.0,
    verbose: bool = False,
    rerank_llm: Any = None,
    keep_per_claim: int = 4,
) -> EvidencePack:
    """Extract claims, retrieve across every query and source, then filter.

    `rerank_llm` should be a cheap backend: relevance filtering is a much easier
    task than judging, and it runs once per claim. Without one, filtering falls
    back to term overlap, which is markedly worse. Never raises; degrades to an
    empty pack.
    """
    claims = extract_claims(llm, proposal_text, max_claims=max_claims)
    if verbose:
        print(f"    [evidence] extracted {len(claims)} claims"
              f"{'（LLM 重排已启用）' if rerank_llm is not None else '（无重排，退化为词面过滤）'}")
    results: Dict[int, List[dict]] = {}
    for i, c in enumerate(claims):
        candidates: List[dict] = []
        for q in c.queries:
            try:
                candidates.extend(search_fn(q, per_query))
            except Exception:
                pass
            if delay:
                time.sleep(delay)
        candidates = _dedupe(candidates)

        if rerank_llm is not None:
            kept = rerank_papers(rerank_llm, c.claim, candidates)
            how = "重排"
        else:
            terms = _query_terms(" ".join(c.queries))
            kept = [p for p in candidates if _is_relevant(p, terms)]
            how = "词面"
        results[i] = kept[:keep_per_claim]

        if verbose:
            print(f"    [evidence] {len(c.queries)} 查询 → {len(candidates)} 候选 "
                  f"→ {how}后 {len(results[i])} 篇 | {c.query[:44]}")
    return EvidencePack(claims=claims, results=results)
