"""Shared paths and helpers for experiment 1 (see experiments/PLAN.md)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

EXP = ROOT / "experiments"
TASKS = EXP / "tasks"
PROMPT_DOC = EXP / "prompts" / "single_system.md"
RUNS = ROOT / "runs" / "exp1"          # runs*/ is gitignored

CONDITIONS = ["C1-CC", "C1-CX", "C2", "C3", "C4"]
GEN_MODEL = "deepseek-flash"


def task_ids() -> List[str]:
    return sorted(p.stem for p in TASKS.glob("task_*.json"))


def load_task(task_id: str) -> Dict[str, Any]:
    return json.loads((TASKS / f"{task_id}.json").read_text(encoding="utf-8"))


def run_dir(condition: str, task_id: str, rep: int) -> Path:
    return RUNS / "gen" / condition / task_id / f"r{rep}"


def run_id(condition: str, task_id: str, rep: int) -> str:
    return f"{condition}__{task_id}__r{rep}"


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path: Path, data: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def git_state() -> Dict[str, Any]:
    """Commit and whether the tree had uncommitted changes when a run started —
    a run on a dirty tree cannot be reproduced from its commit alone."""
    def git(*args: str) -> str:
        try:
            return subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                                  text=True, encoding="utf-8", check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            return ""
    return {"commit": git("rev-parse", "HEAD"), "dirty": bool(git("status", "--porcelain"))}


def gen_env() -> Dict[str, str]:
    """Environment for generation subprocesses: every condition on the same model."""
    env = dict(os.environ)
    env["AI4PROPOSAL_MODEL"] = GEN_MODEL
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env
