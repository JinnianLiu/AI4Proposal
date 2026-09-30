"""Render the unified C1/C4 prompt for a task.

The prompt text lives in exactly one place, prompts/single_system.md, as the
fenced blocks under its "## System", "## User" and "## C1 追加的 system 段"
headings. Parsing the document rather than copying its text into code keeps the
documented prompt and the prompt actually sent from drifting apart.

    uv run python experiments/prompts_render.py            # all tasks -> runs/exp1/prompts/
    uv run python experiments/prompts_render.py task_008
"""
from __future__ import annotations

import re
import sys
from typing import Dict, Tuple

from common import PROMPT_DOC, RUNS, load_task, task_ids

from ai4proposal import writer_prompts as WP

_HEADINGS = {"system": "## System", "user": "## User", "c1": "## C1 追加的 system 段"}


def _block_after(doc: str, heading: str) -> str:
    start = doc.find("\n" + heading)
    if start < 0:
        raise ValueError(f"{PROMPT_DOC.name}: heading {heading!r} not found")
    m = re.search(r"```[a-z]*\n(.*?)\n```", doc[start:], re.S)
    if not m:
        raise ValueError(f"{PROMPT_DOC.name}: no fenced block under {heading!r}")
    return m.group(1)


def prompt_templates() -> Dict[str, str]:
    doc = PROMPT_DOC.read_text(encoding="utf-8")
    return {k: _block_after(doc, h) for k, h in _HEADINGS.items()}


def render(task: Dict) -> Tuple[str, str, str]:
    """(system, user, c1 workspace addendum) for one task."""
    structure = task.get("structure") or {}
    if not structure.get("core_sections"):
        raise ValueError(f"{task.get('task_id')}: experiment tasks must carry a frozen structure")
    v = WP.task_vars(task, structure)
    v["sections"] = WP.sections_outline(structure, task.get("language"))
    t = prompt_templates()
    out = tuple(WP.fill(t[k], v) for k in ("system", "user", "c1"))
    for text in out:
        left = re.findall(r"\$\{(\w+)\}", text)
        if left:
            raise ValueError(f"{task.get('task_id')}: unfilled placeholders {left}")
    return out  # type: ignore[return-value]


def write_rendered(task_id: str) -> None:
    system, user, c1 = render(load_task(task_id))
    out = RUNS / "prompts"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{task_id}.system.md").write_text(system, encoding="utf-8")
    (out / f"{task_id}.user.md").write_text(user, encoding="utf-8")
    (out / "c1_workspace.md").write_text(c1, encoding="utf-8")


if __name__ == "__main__":
    for tid in (sys.argv[1:] or task_ids()):
        write_rendered(tid)
        print(f"rendered {tid} -> {RUNS / 'prompts'}")
