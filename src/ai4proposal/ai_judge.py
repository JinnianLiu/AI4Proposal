"""AI Judge: LLM-based holistic proposal evaluation.

Replaces the deterministic keyword-matching evaluator with an LLM judge
that reads the full proposal and scores it like a real reviewer.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .llm import LLMBackend

JUDGE_SYSTEM_PROMPT = """You are an expert grant proposal reviewer with 20 years of experience reviewing proposals for NSF, NIH, Horizon Europe, and major foundations. You evaluate proposals rigorously but fairly, providing specific, actionable feedback.

For each proposal you review, you:
1. Read the entire proposal carefully
2. Score each dimension independently on a 1-10 scale
3. Provide 1-2 sentences of specific feedback per dimension
4. Note what's missing or could be improved
5. Are consistent in your scoring across proposals

Always respond with valid JSON only. No markdown, no extra text."""

JUDGE_EVAL_PROMPT = """Evaluate this grant proposal against the original funded project it's based on.

## Original Funded Project Abstract
Title: {call_title}
Sponsor: {sponsor}
Abstract: {abstract}

## Generated Proposal
{proposal_text}

## Scoring Instructions
Rate each dimension from 1-10. Be critical - most proposals score 5-7. Reserve 9-10 for truly exceptional work.

Dimensions:
1. **scientific_quality** - Scientific/technical excellence and rigour
2. **feasibility** - Is the approach realistic and well-planned?
3. **innovation** - Novelty and creative thinking
4. **clarity** - Writing quality, structure, and readability
5. **compliance** - Does it cover all expected proposal sections?
6. **impact** - Potential significance and broader impact
7. **alignment** - How well does the proposal capture the project described in the abstract?

Respond with ONLY this JSON:
{{
  "scores": {{
    "scientific_quality": <1-10>,
    "feasibility": <1-10>,
    "innovation": <1-10>,
    "clarity": <1-10>,
    "compliance": <1-10>,
    "impact": <1-10>,
    "alignment": <1-10>
  }},
  "overall_score": <1-10>,
  "strengths": ["..."],
  "weaknesses": ["..."],
  "verdict": "<recommend_submit | revise_resubmit | reject>",
  "summary": "<one paragraph>"
}}"""


@dataclass
class JudgeResult:
    scores: Dict[str, float]
    overall_score: float
    strengths: List[str]
    weaknesses: List[str]
    verdict: str
    summary: str
    raw_response: str = ""


def _parse_judge_response(response: str) -> JudgeResult:
    """Parse LLM judge response into structured result."""
    try:
        start = response.find("{")
        end = response.rfind("}") + 1
        if start >= 0 and end > start:
            data = json.loads(response[start:end])

            scores_raw = data.get("scores", {})
            scores = {}
            for key in [
                "scientific_quality", "feasibility", "innovation",
                "clarity", "compliance", "impact", "team_fit",
            ]:
                scores[key] = float(scores_raw.get(key, 5))

            return JudgeResult(
                scores=scores,
                overall_score=float(data.get("overall_score", sum(scores.values()) / len(scores))),
                strengths=data.get("strengths", []),
                weaknesses=data.get("weaknesses", []),
                verdict=data.get("verdict", "revise_resubmit"),
                summary=data.get("summary", ""),
                raw_response=response,
            )
    except Exception:
        pass

    return JudgeResult(
        scores={},
        overall_score=5.0,
        strengths=[],
        weaknesses=[],
        verdict="error",
        summary="Failed to parse judge response",
        raw_response=response,
    )


class AIJudge:
    """LLM-based proposal judge."""

    def __init__(self, llm: LLMBackend):
        self.llm = llm

    def evaluate(
        self,
        proposal_text: str,
        call: Dict[str, Any],
        team: Dict[str, Any],
    ) -> JudgeResult:
        """Evaluate a proposal against its original abstract."""
        # Truncate proposal if too long
        max_chars = 24000
        if len(proposal_text) > max_chars:
            proposal_text = proposal_text[:max_chars] + "\n\n[... truncated ...]"

        prompt = JUDGE_EVAL_PROMPT.format(
            call_title=call.get("title", ""),
            sponsor=call.get("sponsor", ""),
            abstract=call.get("abstract", "")[:2000],
            proposal_text=proposal_text,
        )

        response = self.llm.generate_text(
            system_prompt=JUDGE_SYSTEM_PROMPT,
            user_prompt=prompt,
        )
        return _parse_judge_response(response)

    def compare(
        self,
        variants: Dict[str, tuple[str, Dict[str, Any], Dict[str, Any]]],
    ) -> Dict[str, JudgeResult]:
        """Evaluate multiple variant proposals for the same call."""
        results = {}
        for variant_name, (proposal_text, call, team) in variants.items():
            results[variant_name] = self.evaluate(proposal_text, call, team)
        return results


def make_judge_from_env() -> Optional["AIJudge"]:
    """Create an AIJudge from environment variables."""
    llm = LLMBackend.from_env()
    if llm is None:
        return None
    return AIJudge(llm)
