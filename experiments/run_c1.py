"""Run condition C1 (single agent) headless, in two frameworks (PLAN §2.1, §2.2).

  C1-CC  Claude Code  → DeepSeek's Anthropic-compatible endpoint
  C1-CX  Codex CLI    → DeepSeek's /responses endpoint

Both get the unified system prompt, the C1 workspace addendum and the same user
message as C4. Each run works in an empty directory and must leave the proposal
in proposal.md. The only intervention allowed is a fixed continuation sentence,
at most twice, when proposal.md is missing chapters.

    uv run python experiments/run_c1.py --frameworks CC CX --tasks task_001

Needs `claude` and/or `codex` on PATH and the DeepSeek key in AI4PROPOSAL_API_KEY.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from common import (GEN_MODEL, RUNS, load_task, now_iso, read_json, run_dir, run_id,
                    task_ids, write_json, git_state)
from generate import _finish
from normalize import check_sections
from prompts_render import render

CONTINUE = "请从中断处继续，完成 proposal.md 中缺少的章节，不要重复已写内容。"
MAX_CONTINUATIONS = 2
DEEPSEEK_ANTHROPIC = "https://api.deepseek.com/anthropic"
DEEPSEEK_OPENAI = "https://api.deepseek.com"
ARGV_LIMIT = 30000   # Windows caps a command line near 32K characters


def _exe(name: str) -> str:
    path = shutil.which(name)
    if not path:
        sys.exit(f"ERROR: `{name}` not found on PATH")
    return path


def _version(exe: str) -> str:
    try:
        return subprocess.run([exe, "--version"], capture_output=True, text=True,
                              encoding="utf-8", timeout=60).stdout.strip()
    except Exception as exc:          # noqa: BLE001 - recorded, not fatal
        return f"unknown ({exc})"


def _key() -> str:
    key = os.environ.get("AI4PROPOSAL_API_KEY", "")
    if not key:
        sys.exit("ERROR: AI4PROPOSAL_API_KEY (the DeepSeek key) not set")
    return key


def _jsonl(path: Path) -> List[Dict[str, Any]]:
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


# ────────────────────────────── Claude Code ──────────────────────────────

def _cc_invoke(exe: str, work: Path, files: Dict[str, Path], prompt: str, log: Path,
               cont: bool) -> int:
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)          # would override the token below
    env.update({"ANTHROPIC_BASE_URL": DEEPSEEK_ANTHROPIC, "ANTHROPIC_AUTH_TOKEN": _key(),
                "ANTHROPIC_MODEL": GEN_MODEL, "PYTHONUTF8": "1"})
    args = [exe, "-p", "--bare", "--model", GEN_MODEL,
            "--system-prompt-file", str(files["system"]),
            "--append-system-prompt-file", str(files["c1"]),
            "--tools", "Read,Write,Edit", "--permission-mode", "acceptEdits",
            "--output-format", "stream-json", "--verbose"]
    if cont:
        args.append("--continue")
    args.append(prompt)
    with log.open("a", encoding="utf-8") as fh:
        return subprocess.run(args, cwd=work, env=env, stdout=fh, stderr=subprocess.STDOUT,
                              text=True, encoding="utf-8").returncode


def _cc_summary(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    results = [e for e in events if e.get("type") == "result"]
    tools = sum(1 for e in events if e.get("type") == "assistant"
                for c in (e.get("message") or {}).get("content") or []
                if isinstance(c, dict) and c.get("type") == "tool_use")
    usage = {"prompt_tokens": 0, "completion_tokens": 0}
    for r in results:
        u = r.get("usage") or {}
        usage["prompt_tokens"] += int(u.get("input_tokens") or 0) + int(u.get("cache_read_input_tokens") or 0)
        usage["completion_tokens"] += int(u.get("output_tokens") or 0)
    return {"turns": sum(int(r.get("num_turns") or 0) for r in results), "tool_calls": tools,
            "is_error": any(r.get("is_error") for r in results), **usage}


# ──────────────────────────────── Codex ────────────────────────────────

def _cx_home(c1_text: str) -> Path:
    """An isolated CODEX_HOME so a personal ~/.codex config never leaks in."""
    home = RUNS / "c1_codex_home"
    home.mkdir(parents=True, exist_ok=True)
    if "'''" in c1_text:    # would end the TOML literal string early
        raise ValueError("C1 addendum contains ''' and cannot be embedded in config.toml")
    dev = c1_text
    (home / "config.toml").write_text(f"""model = "{GEN_MODEL}"
model_provider = "deepseek"
web_search = "disabled"
sandbox_mode = "workspace-write"
approval_policy = "never"
project_doc_max_bytes = 0
developer_instructions = '''
{dev}
'''

[model_providers.deepseek]
name = "DeepSeek"
base_url = "{DEEPSEEK_OPENAI}"
env_key = "DEEPSEEK_API_KEY"
wire_api = "responses"
""", encoding="utf-8")
    return home


def _cx_invoke(exe: str, work: Path, files: Dict[str, Path], prompt: str, log: Path,
               cont: bool, home: Path) -> int:
    env = dict(os.environ)
    env.update({"CODEX_HOME": str(home), "DEEPSEEK_API_KEY": _key(), "PYTHONUTF8": "1"})
    common = ["-c", f'model_instructions_file="{files["system"].as_posix()}"',
              "--skip-git-repo-check", "--json", "-o", str(work.parent / "last_message.txt")]
    if cont:
        args = [exe, "exec", "resume", "--last", *common, prompt]
    else:
        args = [exe, "exec", *common, "--cd", str(work), "-s", "workspace-write", "-"]
    with log.open("a", encoding="utf-8") as fh:
        return subprocess.run(args, cwd=work, env=env, input=None if cont else prompt,
                              stdout=fh, stderr=subprocess.STDOUT, text=True,
                              encoding="utf-8").returncode


def _cx_summary(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    items: Dict[str, int] = {}
    usage = {"prompt_tokens": 0, "completion_tokens": 0}
    for e in events:
        if e.get("type") == "item.completed":
            kind = str((e.get("item") or {}).get("type") or (e.get("item") or {}).get("item_type") or "?")
            items[kind] = items.get(kind, 0) + 1
        if e.get("type") == "turn.completed":
            u = e.get("usage") or {}
            usage["prompt_tokens"] += int(u.get("input_tokens") or 0)
            usage["completion_tokens"] += int(u.get("output_tokens") or 0)
    turns = sum(1 for e in events if e.get("type") == "turn.completed")
    tools = sum(n for k, n in items.items() if k not in ("agent_message", "reasoning"))
    return {"turns": turns, "tool_calls": tools, "items": items,
            "is_error": any(e.get("type") in ("turn.failed", "error") for e in events), **usage}


# ──────────────────────────────── driver ────────────────────────────────

def run_one(fw: str, tid: str, rep: int) -> Dict[str, Any]:
    cond = f"C1-{fw}"
    d = run_dir(cond, tid, rep)
    if d.exists():
        shutil.rmtree(d)
    work = d / "work"
    work.mkdir(parents=True)
    task = load_task(tid)
    write_json(d / "task.json", task)
    system, user, c1 = render(task)
    if len(user) > ARGV_LIMIT and fw == "CC":
        sys.exit(f"ERROR: {tid} user prompt is {len(user)} chars, too long for a command line")
    files = {"system": d / "prompt.system.md", "user": d / "prompt.user.md", "c1": d / "c1_workspace.md"}
    for k, text in (("system", system), ("user", user), ("c1", c1)):
        files[k].write_text(text, encoding="utf-8")

    exe = _exe("claude" if fw == "CC" else "codex")
    record = {"run_id": run_id(cond, tid, rep), "task_id": tid, "condition": cond, "rep": rep,
              "model": GEN_MODEL, "started_at": now_iso(), **git_state(),
              "framework": ("Claude Code " if fw == "CC" else "Codex ") + _version(exe)}
    log = d / "transcript.jsonl"
    home = _cx_home(c1) if fw == "CX" else None

    t0 = time.time()
    exit_codes: List[int] = []
    for step in range(MAX_CONTINUATIONS + 1):
        cont = step > 0
        prompt = CONTINUE if cont else user
        code = (_cc_invoke(exe, work, files, prompt, log, cont) if fw == "CC"
                else _cx_invoke(exe, work, files, prompt, log, cont, home))
        exit_codes.append(code)
        out = work / "proposal.md"
        text = out.read_text(encoding="utf-8") if out.exists() else ""
        # Continue only a run that finished cleanly but left chapters unwritten.
        if code != 0 or (text and not check_sections(text, task)["missing_sections"]):
            break
    continuations = len(exit_codes) - 1

    events = _jsonl(log) if log.exists() else []
    summary = _cc_summary(events) if fw == "CC" else _cx_summary(events)
    record.update({"elapsed_seconds": round(time.time() - t0, 1), "exit_codes": exit_codes,
                   "continuations": continuations, **summary,
                   "llm_calls": summary.get("turns"),
                   "workspace_files": sorted(p.name for p in work.iterdir())})
    extra = []
    if any(c != 0 for c in exit_codes):
        extra.append(f"agent exited non-zero {exit_codes}")
    if summary.get("is_error"):
        extra.append("agent reported an error result")
    out = work / "proposal.md"
    return _finish(d, record, out.read_text(encoding="utf-8") if out.exists() else "", task, extra)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--frameworks", nargs="+", default=["CC", "CX"], choices=["CC", "CX"])
    ap.add_argument("--tasks", nargs="+", default=None)
    ap.add_argument("--rep", type=int, default=1)
    ap.add_argument("--attempts", type=int, default=3)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    for tid in args.tasks or task_ids():
        for fw in args.frameworks:
            cond = f"C1-{fw}"
            prev = read_json(run_dir(cond, tid, args.rep) / "run.json", {}) or {}
            if prev.get("status") == "valid" and not args.force:
                print(f"skip {prev['run_id']} (valid)")
                continue
            for attempt in range(1, args.attempts + 1):
                print(f"== {run_id(cond, tid, args.rep)} attempt {attempt}", flush=True)
                rec = run_one(fw, tid, args.rep)
                rec["attempt"] = attempt
                write_json(run_dir(cond, tid, args.rep) / "run.json", rec)
                print(f"   {rec['status']} | {rec['char_count']} chars | turns {rec.get('turns')} | "
                      f"cont {rec['continuations']} | {rec['void_reasons'] or ''}", flush=True)
                if rec["status"] == "valid":
                    break
    return 0


if __name__ == "__main__":
    sys.exit(main())
