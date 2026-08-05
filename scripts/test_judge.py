#!/usr/bin/env python3
"""Standalone harness for debugging the AI Judge module.

Two modes:

  # 1) Offline self-check (no API key, no network) -- validates the
  #    dimension contract and the parser/evaluate plumbing with a fake LLM.
  python scripts/test_judge.py --offline

  # 2) Real API run -- scores test.md against topic_003.
  #    Set env first:
  #      export AI4PROPOSAL_API_KEY=sk-...
  #      export AI4PROPOSAL_BASE_URL=https://api.openai.com/v1
  #      export AI4PROPOSAL_MODEL=gpt-4.1
  python scripts/test_judge.py --proposal test.md --topic cases/research_topics/topic_003.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from ai4proposal.ai_judge import AIJudge, _parse_judge_response  # noqa: E402
from ai4proposal.llm import LLMBackend  # noqa: E402
from ai4proposal.panel_judge import (  # noqa: E402
    PanelJudge, WEIGHTS, weighted_overall, decide_verdict,
)
from ai4proposal.evidence import (  # noqa: E402
    gather_evidence, extract_claims, EvidencePack, Claim,
)

# Dimensions the single-judge JUDGE_EVAL_PROMPT actually asks the model to output.
PROMPT_DIMENSIONS = [
    "scientific_quality", "feasibility", "innovation",
    "clarity", "compliance", "impact", "alignment",
]

# Dimensions the panel aggregates (must match panel_judge.WEIGHTS keys).
PANEL_DIMENSIONS = list(WEIGHTS.keys())


def build_call(topic_path: Path) -> dict:
    """Reconstruct the `call` dict exactly as scripts/run_pipeline.py does."""
    topic = json.loads(topic_path.read_text(encoding="utf-8"))
    return {
        "case_id": topic.get("topic_id", topic_path.stem),
        "title": topic["title"],
        "sponsor": topic.get("sponsor", "NSFC"),
        "abstract": topic["background"],          # note: abstract == background
        "budget": topic.get("budget", {}),
        "keywords": [],
        "language": topic.get("language", "zh"),
        # Not consumed by the current AIJudge, kept for improvement work:
        "challenges": topic.get("challenges", []),
        "references": topic.get("references", []),
    }


def print_result(jr) -> None:
    print("\n=== AI Judge Result ===")
    print(f"overall_score : {jr.overall_score}  (-> quality_score {round(jr.overall_score * 10, 1)}/100)")
    print(f"verdict       : {jr.verdict}")
    print("scores:")
    for k, v in jr.scores.items():
        print(f"  {k:<20} {v}")
    print("strengths:")
    for s in jr.strengths:
        print(f"  + {s}")
    print("weaknesses:")
    for w in jr.weaknesses:
        print(f"  - {w}")
    print(f"summary: {jr.summary}")
    # Panel-only extras (duck-typed):
    cov = getattr(jr, "challenge_coverage", None)
    if cov:
        print("challenge_coverage:")
        for c in cov:
            print(f"  [{c.get('level','?'):<7}] {c.get('challenge','')}")
    chk = getattr(jr, "section_checklist", None)
    if chk:
        missing = [c.get("section") for c in chk if c.get("present") is False]
        print(f"sections missing: {missing if missing else 'none'}")


# ─────────────────────────────── offline ───────────────────────────────
class FakeLLM:
    """Records the prompt and returns a canned judge JSON."""
    def __init__(self, response: str):
        self.response = response
        self.last_system = None
        self.last_user = None

    def generate_text(self, system_prompt: str, user_prompt: str) -> str:
        self.last_system = system_prompt
        self.last_user = user_prompt
        return self.response


def run_offline() -> int:
    print("[offline] parser + evaluate plumbing check (no network)\n")
    ok = True

    canned = json.dumps({
        "scores": {d: 8 for d in PROMPT_DIMENSIONS},
        "overall_score": 7.5,
        "strengths": ["clear objectives"],
        "weaknesses": ["thin budget justification"],
        "verdict": "revise_resubmit",
        "summary": "solid but needs work",
    })

    jr = _parse_judge_response(canned)

    # Contract check: every dimension the prompt asks for should survive parsing.
    dropped = [d for d in PROMPT_DIMENSIONS if d not in jr.scores]
    phantom = [k for k in jr.scores if k not in PROMPT_DIMENSIONS]
    if dropped:
        print(f"  [FAIL] dimensions asked-for but DROPPED by parser: {dropped}")
        ok = False
    if phantom:
        print(f"  [FAIL] dimensions parser expects but prompt never emits (always default 5): {phantom}")
        ok = False
    if not dropped and not phantom:
        print("  [ok] dimension contract consistent")

    # Plumbing check: does challenges make it into the prompt? (currently no)
    fake = FakeLLM(canned)
    judge = AIJudge(fake)
    call = {"title": "T", "sponsor": "NSFC", "abstract": "A" * 100, "challenges": ["CH-MARKER"]}
    judge.evaluate("proposal body", call, {})
    if "CH-MARKER" in (fake.last_user or ""):
        print("  [ok] challenges reach the judge prompt")
    else:
        print("  [info] challenges are NOT passed to the judge prompt (expected today)")

    print(f"\n[single] {'PASS' if ok else 'FAIL — see dimension bug in ai_judge.py'}")

    ok_panel = run_offline_panel()
    ok_evid = run_offline_evidence()
    return 0 if (ok and ok_panel and ok_evid) else 1


class RoutingFakeLLM:
    """Returns a canned JSON per panel role, keyed by a marker in the system prompt."""
    def __init__(self, by_role: dict, chair: str):
        self.by_role = by_role
        self.chair = chair
        self.seen_challenges = False
        self.role_prompts = {}  # role → last user prompt seen

    def generate_text(self, system_prompt: str, user_prompt: str) -> str:
        if "会评专家组组长" in system_prompt:
            return self.chair
        for name in self.by_role:
            if _sig_in_prompt(name, system_prompt):
                self.role_prompts[name] = user_prompt
                if name == "method_feasibility" and "CH-MARKER" in user_prompt:
                    self.seen_challenges = True
                return self.by_role[name]
        return "{}"


def _sig_in_prompt(role: str, system_prompt: str) -> bool:
    sig = {
        "science_innovation": "研究价值与创新性",
        "method_feasibility": "技术方案与可行性",
        "kpi_requirements": "考核指标与内容具体性",
        "compliance_writing": "形式审查与写作",
    }[role]
    return sig in system_prompt


def run_offline_panel() -> bool:
    print("\n[panel] aggregation + plumbing check (no network)\n")
    ok = True

    # Canned specialist outputs: all 7-8s, but one hard requirement marked "none".
    by_role = _canned_roles(req_none=True)
    chair = json.dumps({"strengths": ["s1"], "weaknesses": ["w1"], "summary": "sum"})

    fake = RoutingFakeLLM(by_role, chair)
    judge = PanelJudge(fake)
    task = {"title": "T", "program": "P", "background": "A" * 100, "challenges": ["CH-MARKER challenge"]}
    res = judge.evaluate("proposal body", task)

    # 1) all 8 panel dims populated
    missing = [d for d in PANEL_DIMENSIONS if d not in res.scores]
    if missing:
        print(f"  [FAIL] panel dims missing after aggregation: {missing}")
        ok = False
    else:
        print("  [ok] all 8 panel dimensions populated")

    # 2) weighted overall matches independent recompute
    expected = weighted_overall({k: res.scores[k] for k in PANEL_DIMENSIONS})
    if abs(expected - res.overall_score) < 1e-9:
        print(f"  [ok] weighted overall reproducible ({res.overall_score})")
    else:
        print(f"  [FAIL] overall {res.overall_score} != recompute {expected}")
        ok = False

    # 3) an uncovered hard requirement must cap verdict below recommend_submit
    if res.verdict != "recommend_submit":
        print(f"  [ok] uncovered-requirement override active (verdict={res.verdict})")
    else:
        print("  [FAIL] uncovered requirement did NOT cap the verdict")
        ok = False

    # 4) verdict matches the deterministic rule
    want = decide_verdict(res.overall_score, has_uncovered_requirement=True, has_missing_section=False)
    if want == res.verdict:
        print(f"  [ok] verdict matches decide_verdict ({res.verdict})")
    else:
        print(f"  [FAIL] verdict {res.verdict} != rule {want}")
        ok = False

    # 5) challenges reach the method judge
    if fake.seen_challenges:
        print("  [ok] challenges reach the method/feasibility judge")
    else:
        print("  [FAIL] challenges NOT passed to the panel")
        ok = False

    print(f"\n[panel] {'PASS' if ok else 'FAIL'}")
    return ok


class ClaimFakeLLM:
    """Returns a canned claim-extraction JSON (for evidence tests)."""
    def __init__(self, response: str):
        self.response = response

    def generate_text(self, system_prompt: str, user_prompt: str) -> str:
        return self.response


def _fake_search(query: str, limit: int = 3):
    return [{"title": f"PAPER::{query[:20]}", "authors": "A, B", "year": 2024,
             "venue": "NeurIPS", "abstract": "canned abstract", "citations": 123}]


def run_offline_evidence() -> bool:
    print("\n[evidence] extraction + retrieval + injection check (no network)\n")
    ok = True

    claim_json = json.dumps({"claims": [
        {"claim": "首次提出X机制", "query": "novel X mechanism LLM", "type": "novelty"},
        {"claim": "成功率≥85%", "query": "success rate benchmark", "type": "metric"},
        {"claim": "基于GCG方法", "query": "GCG attack method", "type": "method"},
    ]})

    # 1) extraction parses claims & routes by type (metric -> kpi_requirements now)
    claims = extract_claims(ClaimFakeLLM(claim_json), "proposal body")
    routed = [c.role for c in claims]
    if routed == ["science_innovation", "kpi_requirements", "method_feasibility"]:
        print("  [ok] claims extracted & routed by type")
    else:
        print(f"  [FAIL] extraction/routing wrong: {[(c.type, c.role) for c in claims]}")
        ok = False

    # 2) gather_evidence with fake search (no delay) builds cards containing paper titles
    pack = gather_evidence(ClaimFakeLLM(claim_json), "proposal body",
                           search_fn=_fake_search, delay=0)
    if all("PAPER::" in pack.for_role(r)
           for r in ("science_innovation", "kpi_requirements", "method_feasibility")):
        print("  [ok] evidence cards carry retrieved papers")
    else:
        print("  [FAIL] evidence cards missing papers")
        ok = False

    # 3) evidence actually reaches the science / method / kpi judge prompts, not compliance
    fake = RoutingFakeLLM(_canned_roles(), json.dumps({"strengths": [], "weaknesses": [], "summary": ""}))
    judge = PanelJudge(fake)
    task = {"title": "T", "program": "P", "background": "A" * 50, "challenges": ["c1"]}
    judge.evaluate("proposal body", task, evidence=pack)
    inj = {r: ("PAPER::" in fake.role_prompts.get(r, ""))
           for r in ("science_innovation", "method_feasibility", "kpi_requirements", "compliance_writing")}
    if inj["science_innovation"] and inj["method_feasibility"] and inj["kpi_requirements"] and not inj["compliance_writing"]:
        print("  [ok] evidence injected into science+method+kpi judges only")
    else:
        print(f"  [FAIL] injection wrong: {inj}")
        ok = False

    # 4) evidence=None → neutral placeholder, no crash
    fake2 = RoutingFakeLLM(_canned_roles(), json.dumps({"strengths": [], "weaknesses": [], "summary": ""}))
    PanelJudge(fake2).evaluate("proposal body", task, evidence=None)
    if "PAPER::" not in fake2.role_prompts.get("science_innovation", ""):
        print("  [ok] evidence=None degrades to neutral placeholder")
    else:
        print("  [FAIL] evidence leaked when None")
        ok = False

    print(f"\n[evidence] {'PASS' if ok else 'FAIL'}")
    return ok


def _canned_roles(req_none: bool = False) -> dict:
    rc = [{"requirement": "r1", "level": "covered", "evidence": "e"}]
    if req_none:
        rc.append({"requirement": "r2", "level": "none", "evidence": "e"})
    return {
        "science_innovation": json.dumps({"role": "science_innovation",
            "dimensions": {"research_value": {"reason": "r", "score": 7},
                           "innovation": {"reason": "r", "score": 6}},
            "over_claims": [], "notes": "n"}),
        "method_feasibility": json.dumps({"role": "method_feasibility",
            "dimensions": {"methodology": {"reason": "r", "score": 7},
                           "feasibility": {"reason": "r", "score": 7}},
            "challenge_coverage": [], "weak_points": [], "notes": "n"}),
        "kpi_requirements": json.dumps({"role": "kpi_requirements",
            "dimensions": {"kpi_rigor": {"reason": "r", "score": 7},
                           "specificity": {"reason": "r", "score": 7}},
            "requirements_coverage": rc, "empty_phrases": [], "notes": "n"}),
        "compliance_writing": json.dumps({"role": "compliance_writing",
            "dimensions": {"compliance": {"reason": "r", "score": 8},
                           "clarity": {"reason": "r", "score": 8}},
            "section_checklist": [{"section": "课题目标", "present": True, "substance": "s"}],
            "constraint_violations": [], "notes": "n"}),
    }


# ─────────────────────────────── real ──────────────────────────────────
def make_backend() -> LLMBackend:
    api_key = (os.getenv("AI4PROPOSAL_API_KEY") or os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        print("Error: set AI4PROPOSAL_API_KEY (or OPENAI_API_KEY) for a real run, "
              "or use --offline.")
        sys.exit(2)
    model = os.getenv("AI4PROPOSAL_MODEL", "gpt-4.1").strip()
    base_url = (os.getenv("AI4PROPOSAL_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "").strip() or None
    timeout = float(os.getenv("AI4PROPOSAL_TIMEOUT_SECONDS", "180"))
    print(f"[real] model={model} base_url={base_url or 'default'}")
    return LLMBackend(model=model, api_key=api_key, base_url=base_url, timeout_seconds=timeout)


def run_real(proposal_path: Path, topic_path: Path, mode: str, use_evidence: bool) -> int:
    proposal_text = proposal_path.read_text(encoding="utf-8")
    call = build_call(topic_path)
    print(f"[real] proposal={proposal_path.name} ({len(proposal_text)} chars) "
          f"topic={call['case_id']} mode={mode} evidence={use_evidence}")

    backend = make_backend()

    evidence = None
    if use_evidence:
        if mode != "panel":
            print("[real] --evidence only applies to --panel; ignoring")
        else:
            print("[real] gathering external evidence (Semantic Scholar) ...")
            evidence = gather_evidence(backend, proposal_text, verbose=True)
            print(f"[real] evidence: {len(evidence.claims)} claims retrieved")

    judge = PanelJudge(backend, verbose=True) if mode == "panel" else AIJudge(backend)
    try:
        if mode == "panel":
            jr = judge.evaluate(proposal_text, call, evidence=evidence)  # panel reads the task/call directly
        else:
            jr = judge.evaluate(proposal_text, call, {})
    except Exception as e:  # evaluate has no internal retry
        print(f"\n[real] evaluate() raised: {type(e).__name__}: {e}")
        return 1

    print_result(jr)
    if jr.verdict == "error":
        print("\n[real] parser failed — raw model response:")
        print(jr.raw_response[:2000])
        return 1
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Debug harness for ai_judge / panel_judge")
    p.add_argument("--offline", action="store_true", help="Run offline self-check (no API)")
    p.add_argument("--panel", action="store_true", help="Use the multi-judge PanelJudge (else single AIJudge)")
    p.add_argument("--evidence", action="store_true", help="Retrieve external evidence (panel mode only)")
    p.add_argument("--proposal", default="test.md", help="Proposal markdown file")
    p.add_argument("--topic", default="cases/research_topics/topic_003.json", help="Topic JSON")
    args = p.parse_args()

    if args.offline:
        return run_offline()
    resolve = lambda x: Path(x) if Path(x).is_absolute() else ROOT / x
    return run_real(resolve(args.proposal), resolve(args.topic),
                    mode="panel" if args.panel else "single", use_evidence=args.evidence)


if __name__ == "__main__":
    sys.exit(main())


'''

$env:AI4PROPOSAL_API_KEY="sk-your-key-here"
$env:AI4PROPOSAL_BASE_URL="https://api.deepseek.com"
$env:AI4PROPOSAL_MODEL="deepseek-v4-pro"
$env:PYTHONPATH="src"
python scripts/test_judge.py --topic topic_003 --proposal test.md
'''