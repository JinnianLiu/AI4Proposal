#!/usr/bin/env python3
"""Offline self-check for the rubric evaluation framework. No API key, no network.

Validates the scoring contract with a fake backend: dimension/weight/judge
coverage, code-side aggregation and hard gates, the full evaluate() path, prompt
scoping, evidence formatting, and graceful degradation when judges fail.

  python scripts/test_evaluation.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from ai4proposal.evaluation import (  # noqa: E402
    JUDGES, MIN_ALIGNMENT, RUBRICS, WEIGHTS, DIMENSION_NAMES, RubricPanel,
    _science_evidence, decide_verdict, weighted_overall,
)
from ai4proposal.evidence import (  # noqa: E402
    Claim, EvidencePack, NEUTRAL_EVIDENCE, _is_relevant, _query_terms,
)

TASK = {
    "title": "T", "program": "P", "background": "B",
    "requirements": ["开源发布"], "constraints": ["周期1年"], "challenges": ["c1"],
    "structure": {"core_sections": [{"id": "objectives", "name": "课题目标"}]},
}

CANNED = {
    "science": {"role": "science", "dimensions": {
        "scientific_quality": {"reason": "r", "score": 8},
        "innovation": {"reason": "r", "score": 7}},
        "over_claims": ["首次实现"], "hypotheses": ["H1"]},
    "value": {"role": "value", "dimensions": {
        "impact": {"reason": "r", "score": 7},
        "alignment": {"reason": "r", "score": 9}},
        "requirement_coverage": [{"requirement": "开源发布", "covered": False, "where": ""}],
        "off_topic": []},
    "feasibility": {"role": "feasibility", "dimensions": {
        "feasibility": {"reason": "r", "score": 6}},
        "risky_targets": ["30x加速"], "mitigations": []},
    "writing": {"role": "writing", "dimensions": {
        "clarity": {"reason": "r", "score": 9},
        "compliance": {"reason": "r", "score": 8}},
        "section_checklist": [{"section": "课题目标", "present": True, "note": ""}],
        "placeholders": [], "constraint_violations": []},
}

CHAIR = {"strengths": ["s1"], "weaknesses": ["w1"], "summary": "总评"}


class FakePanel:
    """Dispatches on the role name embedded in each judge's output schema."""

    def __init__(self):
        self.prompts = {}

    def generate_text(self, system_prompt, user_prompt):
        for role, payload in CANNED.items():
            if f'"role": "{role}"' in user_prompt:
                self.prompts[role] = user_prompt
                return json.dumps(payload, ensure_ascii=False)
        return json.dumps(CHAIR, ensure_ascii=False)


class BrokenLLM:
    def generate_text(self, **kw):
        raise RuntimeError("api down")


def check(label, cond):
    print(f"  [{'ok' if cond else 'FAIL'}] {label}")
    return bool(cond)


def main() -> int:
    ok = True
    print("\n== contract ==")
    ok &= check("weights sum to 1.00", abs(sum(WEIGHTS.values()) - 1.0) < 1e-9)
    ok &= check("dimension sets agree across WEIGHTS/RUBRICS/NAMES",
                set(WEIGHTS) == set(RUBRICS) == set(DIMENSION_NAMES))
    owned = [d for _, _, _, dims, _ in JUDGES for d in dims]
    ok &= check("judges cover every dimension exactly once",
                sorted(owned) == sorted(WEIGHTS) and len(owned) == len(set(owned)))
    ok &= check("every rubric carries all five anchor bands",
                all(all(b in RUBRICS[d] for b in ("9-10", "7-8", "5-6", "3-4", "1-2"))
                    for d in RUBRICS))

    print("\n== aggregation & gates ==")
    ok &= check("all-8s → 8.0", weighted_overall({d: 8.0 for d in WEIGHTS}) == 8.0)
    ok &= check("missing dims redistribute weight",
                3.0 < weighted_overall({"scientific_quality": 9.0, "clarity": 3.0}) < 9.0)
    ok &= check("clean high score recommends submit",
                decide_verdict(8.0, False, False, False) == "recommend_submit")
    ok &= check("placeholder blocks submit", decide_verdict(8.0, False, False, True) == "revise_resubmit")
    ok &= check("unmet requirement blocks submit", decide_verdict(8.0, True, False, False) == "revise_resubmit")
    ok &= check("missing section blocks submit", decide_verdict(8.0, False, True, False) == "revise_resubmit")
    ok &= check("below 6.0 rejects", decide_verdict(5.9, False, False, False) == "reject")
    ok &= check("failing alignment rejects however high the total",
                decide_verdict(9.5, False, False, False, alignment=1.0) == "reject")
    ok &= check("alignment at the threshold does not reject",
                decide_verdict(8.0, False, False, False, alignment=MIN_ALIGNMENT) == "recommend_submit")
    ok &= check("absent alignment leaves the gate inactive",
                decide_verdict(8.0, False, False, False, alignment=None) == "recommend_submit")

    print("\n== evaluate() ==")
    fake = FakePanel()
    r = RubricPanel(fake).evaluate("PROPOSAL_BODY 正文", TASK)
    expect = round(sum(r.scores[d] * WEIGHTS[d] for d in WEIGHTS), 2)
    ok &= check("all 7 dimensions scored", len(r.scores) == 7)
    ok &= check(f"overall computed in code ({r.overall_score})", r.overall_score == expect)
    ok &= check("0-100 rescale", r.overall_100 == round(expect * 10, 1))
    ok &= check("verdict gated by uncovered requirement", r.verdict == "revise_resubmit")
    ok &= check("structured findings collected",
                r.over_claims == ["首次实现"] and r.risky_targets == ["30x加速"])
    ok &= check("chair output merged", r.strengths == ["s1"] and r.summary == "总评")
    ok &= check("no errors on the happy path", not r.errors)
    try:
        json.dumps(r.to_dict(), ensure_ascii=False)
        ok &= check("result is JSON-serialisable", True)
    except Exception as e:
        ok &= check(f"result is JSON-serialisable ({e})", False)

    print("\n== prompt scoping ==")
    ok &= check("all four judges were called", len(fake.prompts) == 4)
    ok &= check("out-of-scope guard in every judge prompt",
                all("不得作为任何维度的扣分理由" in p for p in fake.prompts.values()))
    ok &= check("full document reaches every judge",
                all("PROPOSAL_BODY" in p for p in fake.prompts.values()))
    ok &= check("task context injected (no unsubstituted ${...})",
                all("${" not in p for p in fake.prompts.values()))
    ok &= check("evidence goes only to the science judge",
                "外部文献证据" in fake.prompts["science"]
                and not any("外部文献证据" in p for k, p in fake.prompts.items() if k != "science"))
    ok &= check("dimension-isolation rule in every judge prompt",
                all("不属于你所负责维度的缺陷，一律不得影响你的分数" in p
                    for p in fake.prompts.values()))
    ok &= check("off-topic bleed blocked for the form judge",
                "不得因跑题而压低本维度" in fake.prompts["writing"])
    ok &= check("9-10 band called out as rare", all("应当罕见" in p for p in fake.prompts.values()))
    ok &= check("retrieval-recency caveat reaches the science judge",
                "检索不到 ≠ 不存在" in fake.prompts["science"])

    print("\n== evidence ==")
    ok &= check("no pack → neutral", _science_evidence(None) == NEUTRAL_EVIDENCE)
    ok &= check("empty pack → neutral", _science_evidence(EvidencePack()) == NEUTRAL_EVIDENCE)
    pack = EvidencePack(
        claims=[Claim(claim="首次提出X", queries=["x method", "x alternative"], type="novelty")],
        results={0: [{"title": "A Prior Work On X", "year": 2024, "venue": "ICML",
                      "authors": "Doe", "citations": 12, "abstract": "we propose x"}]})
    cards = _science_evidence(pack)
    ok &= check("cards carry claim and retrieved paper",
                "首次提出X" in cards and "A Prior Work On X" in cards)
    ok &= check("type filter works", pack.format_cards(types=("metric",)) == NEUTRAL_EVIDENCE)
    ok &= check("cards warn that hits are unvetted keyword matches",
                "未经人工筛选" in cards)

    print("\n== relevance filtering ==")
    terms = _query_terms("World-in-World high visual quality does not equal task success")
    ok &= check("stopwords dropped, technical terms kept",
                "high" not in terms and "does" not in terms and "visual" in terms)
    # the real failure this guards against: a 19k-citation paper sharing one word
    ok &= check("high-citation near-miss rejected",
                not _is_relevant({"title": "The Pascal Visual Object Classes (VOC) Challenge",
                                  "abstract": "A benchmark for object category recognition."}, terms))
    ok &= check("genuine hit kept",
                _is_relevant({"title": "World-in-World: closed-loop evaluation",
                              "abstract": "visual quality does not predict task success"}, terms))
    ok &= check("no terms → keep (never filter on an empty query)",
                _is_relevant({"title": "anything"}, []))
    short = _query_terms("paged attention KV cache")
    ok &= check("short query needs 2 terms, not all of them",
                _is_relevant({"title": "Paged attention for serving", "abstract": "KV reuse"}, short)
                and not _is_relevant({"title": "Attention is all you need", "abstract": ""}, short))

    print("\n== degradation ==")
    r2 = RubricPanel(BrokenLLM(), max_retries=0).evaluate("body", TASK)
    ok &= check("judge failure does not raise", r2.scores == {} and r2.overall_score == 5.0)
    ok &= check("failures recorded in errors", len(r2.errors) >= 4)

    # a judge that answers but omits a dimension used to vanish silently: the
    # weight was redistributed and the run looked clean.
    class PartialLLM:
        def generate_text(self, system_prompt, user_prompt):
            if '"role": "feasibility"' in user_prompt:
                return json.dumps({"role": "feasibility",
                                   "dimensions": {"feasibility": {"reason": "r"}}})
            return FakePanel().generate_text(system_prompt, user_prompt)

    r3 = RubricPanel(PartialLLM()).evaluate("body", TASK)
    ok &= check("unscored dimension is dropped from scores", "feasibility" not in r3.scores)
    ok &= check("unscored dimension is reported rather than silently dropped",
                any("feasibility" in e and "unscored" in e for e in r3.errors))

    print("\n" + ("PASS" if ok else "FAIL") + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
