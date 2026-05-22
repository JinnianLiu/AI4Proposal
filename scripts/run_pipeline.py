#!/usr/bin/env python3
"""Run AI4Proposal V4 Pipeline on a research topic.

Usage:
  # Set env vars first:
  export AI4PROPOSAL_API_KEY=sk-...
  export AI4PROPOSAL_BASE_URL=https://api.openai.com/v1
  export AI4PROPOSAL_MODEL=gpt-4.1

  # Run on a topic:
  python scripts/run_pipeline.py --topic-id topic_003 --output runs/my_case

  # Run with custom team:
  python scripts/run_pipeline.py --topic-id topic_021 --team scripts/example_team.json --output runs/biomed
"""
import argparse, json, sys, os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from ai4proposal.pipeline_v4 import PipelineConfig, run_case


def main():
    parser = argparse.ArgumentParser(description="AI4Proposal V4 Pipeline")
    parser.add_argument("--topic-id", required=True, help="Topic ID (e.g. topic_003)")
    parser.add_argument("--topics-dir", default="cases/research_topics", help="Path to topics directory")
    parser.add_argument("--team", default=None, help="Path to team.json (optional)")
    parser.add_argument("--output", default="runs/latest", help="Output directory")
    parser.add_argument("--no-review", action="store_true", help="Disable review loop")
    args = parser.parse_args()

    # Load topic
    topic_path = Path(args.topics_dir) / f"{args.topic_id}.json"
    if not topic_path.exists():
        print(f"Error: topic not found: {topic_path}")
        sys.exit(1)
    topic = json.loads(topic_path.read_text())

    # Build call
    call = {
        "case_id": args.topic_id,
        "title": topic["title"],
        "sponsor": topic.get("sponsor", "NSFC"),
        "abstract": topic["background"],
        "budget": topic.get("budget", {}),
        "keywords": [],
        "language": topic.get("language", "zh"),
    }

    # Build or load team
    if args.team and Path(args.team).exists():
        team = json.loads(Path(args.team).read_text())
    else:
        team = {
            "institution": "Example University",
            "pi_name": "PI Name",
            "phd_students": [{"name": "Student A", "role": "PhD"}],
            "master_students": [{"name": "Student B", "role": "Master"}],
        }

    # Run pipeline
    config = PipelineConfig.from_env()
    if args.no_review:
        config.use_review = False

    case_dir = Path(f"/tmp/ai4proposal_{args.topic_id}")
    case_dir.mkdir(parents=True, exist_ok=True)
    json.dump(call, (case_dir / "call.json").open("w"), indent=2, ensure_ascii=False)
    json.dump(team, (case_dir / "team.json").open("w"), indent=2, ensure_ascii=False)

    print(f"Running: {topic['title'][:80]}")
    print(f"Model: {config.model} | Review: {config.use_review}")
    score = run_case(case_dir, Path(args.output), config)

    print(f"\nDone.")
    print(f"  Quality: {score.get('quality_score')}")
    print(f"  Verdict: {score.get('verdict')}")
    print(f"  Words: {score['basic']['word_count']}")
    print(f"  Output: {args.output}/proposal_final.md")


if __name__ == "__main__":
    main()
