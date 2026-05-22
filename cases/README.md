# Case Pack Guidance

The bundled cases are bootstrap cases. They are intentionally marked with explicit provenance and should be replaced or extended with real public awarded proposals as you curate the benchmark.

The file `benchmark_registry.json` is the planned case pool, not a finished gold benchmark. It is a machine-readable registry of 8 domains and 100+ topic blueprints that should be progressively curated into full case directories.

The file `curation_batch_20.json` is the current balanced first-pass curation queue. Use `materialize-batch` to generate runnable staging cases under a separate directory such as `cases/staging/` without polluting the main curated case index.

The file `topic_bank_frontier_2025_2026.json` is a fresher source-backed topic bank for benchmark expansion. It focuses on 2025-2026 official or public signals across 8 domains and keeps one `public_funder`, one `industry_university`, and one `hybrid_translational` seed per domain.

Each case directory must contain:

- `opportunity.json`
- `team_profiles.json`
- `references.jsonl`
- `rubric.json`
- `funded_outcome.json`
- `label_bundle.json`
- `gold/`
- `reviews/`
- `reviews/review_packet.json`

Provenance requirements for each case:

- `origin_type`: `public_awarded`, `public_guideline_plus_expert_reconstruction`, or `bootstrap_synthetic`
- `basis_urls`: official or primary-source URLs used to build the case
- `note`: what is real, what is reconstructed, and what still needs replacement

Recommended supervision layers:

- hard labels:
  - section requirements
  - budget limits
  - eligibility and compliance rules
- silver labels:
  - funded outcome summary
  - awardee metadata
  - downstream outputs
- human labels:
  - review packet
  - pairwise preference
  - edit-load annotation
