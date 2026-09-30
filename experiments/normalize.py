"""Normalize a generated proposal before judging, and check it is a valid run.

Deletion only, never rewriting (PLAN §3.3): chat text before the title and
after the last chapter, and leftover [figure:] markers. Everything removed is
returned so the run record can show exactly what the judges did not see.

Validity is a separate question from quality. A run is void when the
*infrastructure* failed — an API call gave up and the pipeline wrote its
[待补充] fallback, or Step 0 fell back — never because the model wrote badly.
A missing chapter in a baseline is the baseline's own result and is judged.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

FIGURE_MARKER = re.compile(r"\[figure:[^\]]*\]")
PIPELINE_FAILURE = "[待补充]"

# A trailing paragraph is dropped only when it is recognisably the model talking
# to the user about the document, not part of the document.
_CHAT_TAIL = re.compile(
    r"^(?:以上(?:是|为|即|就是)|希望(?:这份|以上|本)|如(?:需|果您|有需要|有任何)|如需进一步|"
    r"需要我|(?:注|说明)[:：]\s*本(?:申请书|文档|稿)|"
    r"I hope|Let me know|If you(?:'d| would)? like|Feel free|Please let me know|"
    r"This (?:draft|proposal|document) (?:is|was|has been)|Note: this (?:draft|document))",
    re.I)
_RULE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
_FENCE = re.compile(r"^\s*```[\w-]*\s*$")


def _strip_outer_fence(text: str) -> Tuple[str, str]:
    """Some models wrap the whole answer in ```markdown ... ```."""
    lines = text.strip("\n").split("\n")
    if len(lines) >= 2 and _FENCE.match(lines[0]) and _FENCE.match(lines[-1]):
        return "\n".join(lines[1:-1]), lines[0] + " … " + lines[-1]
    return text, ""


def normalize(raw: str, task: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    removed: List[str] = []
    text, fence = _strip_outer_fence(raw)
    if fence:
        removed.append(f"[outer code fence] {fence}")

    lines = text.split("\n")
    # Leading chatter: everything before the first heading. The title (#) is
    # preferred; a document that starts straight at a chapter (##) keeps it.
    first = next((i for i, l in enumerate(lines) if re.match(r"^#\s", l)), None)
    if first is None:
        first = next((i for i, l in enumerate(lines) if re.match(r"^##\s", l)), 0)
    head = "\n".join(lines[:first]).strip()
    if head:
        removed.append(f"[before title] {head}")
    body = "\n".join(lines[first:])

    # Trailing chatter: peel paragraphs off the end while they are chat or rules.
    paras = re.split(r"\n\s*\n", body.rstrip())
    tail: List[str] = []
    while len(paras) > 1:
        last = paras[-1].strip()
        if _RULE.match(last) or _CHAT_TAIL.match(last):
            tail.insert(0, paras.pop())
        else:
            break
    if tail:
        removed.append("[after last chapter] " + "\n\n".join(t.strip() for t in tail))
    body = "\n\n".join(paras)

    markers = FIGURE_MARKER.findall(body)
    if markers:
        body = FIGURE_MARKER.sub("", body)
        removed.append(f"[figure markers] {len(markers)} removed")
    body = re.sub(r"\n{3,}", "\n\n", body).strip() + "\n"

    report = {
        "removed": removed,
        # Whitespace tidying can make the result a character longer; not a removal.
        "removed_chars": max(0, len(raw) - len(body)),
        **check_sections(body, task),
    }
    return body, report


def _norm_heading(s: str) -> str:
    return re.sub(r"[\s　:：·.、]", "", s).lower()


def check_sections(text: str, task: Dict[str, Any]) -> Dict[str, Any]:
    """Which mandated chapters appear as `## <name>` headings. Recorded only —
    a missing chapter is judged, not voided."""
    expected = [s.get("name", "") for s in (task.get("structure") or {}).get("core_sections") or []]
    found = [m.group(1).strip() for m in re.finditer(r"^##\s+(.+?)\s*$", text, re.M)]
    found_n = {_norm_heading(f) for f in found}
    missing = [e for e in expected if _norm_heading(e) not in found_n]
    extra = [f for f in found if _norm_heading(f) not in {_norm_heading(e) for e in expected}]
    return {"expected_sections": expected, "missing_sections": missing, "extra_h2": extra}


def over_limit_sections(text: str, task: Dict[str, Any], tolerance: float = 0.0) -> List[Dict[str, Any]]:
    """Chapters longer than their word_limit. Descriptive only (no penalty, PLAN §3.5):
    Chinese counts characters, English counts words."""
    lang = str(task.get("language") or "zh")[:2]
    chunks = re.split(r"^##\s+(.+?)\s*$", text, flags=re.M)
    by_name = {_norm_heading(chunks[i]): chunks[i + 1] for i in range(1, len(chunks) - 1, 2)}
    out = []
    for s in (task.get("structure") or {}).get("core_sections") or []:
        limit = s.get("word_limit")
        body = by_name.get(_norm_heading(s.get("name", "")))
        if not limit or body is None:
            continue
        n = len(re.findall(r"[A-Za-z0-9'-]+", body)) if lang == "en" else len(re.sub(r"\s", "", body))
        if n > limit * (1 + tolerance):
            out.append({"section": s.get("name"), "limit": limit, "actual": n,
                        "ratio": round(n / limit - 1, 2)})
    return out


def void_reasons(text: str, pipeline_result: Dict[str, Any] | None = None) -> List[str]:
    """Infrastructure failures that make a run unusable (PLAN §3.5 item 3)."""
    reasons = []
    if not text.strip():
        reasons.append("empty output")
    if PIPELINE_FAILURE in text:
        reasons.append("pipeline fallback placeholder [待补充] present (a section call gave up)")
    r = pipeline_result or {}
    if r.get("failed_sections"):
        reasons.append(f"failed sections: {r['failed_sections']}")
    if r.get("blueprint_fallback"):
        reasons.append("Step 0 blueprint fell back (parse failure)")
    return reasons
