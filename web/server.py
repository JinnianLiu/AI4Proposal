#!/usr/bin/env python3
"""FastAPI server for the AI4Proposal agent.

Jobs shell out to the existing CLIs with the running interpreter rather than
importing them, so the pipeline stays free to change and a crashing job cannot
take the server down.

    uv run python web/server.py                          # http://127.0.0.1:8000
    uv run python web/server.py --debug                  # 生成阶段回放已有产物，秒出结果
    uv run python web/server.py --demo task_001_v20260901  # 演示：前半段真跑，正文回放

Debug mode exists because a real run takes 10-15 minutes, which makes iterating
on the front end impossible. It replays a completed job through the same stage
progression and the same result payload, so every screen after "开始生成" is
exercised without spending a single token. The UI shows a badge whenever it is
on, so a replay is never mistaken for a real run.

Demo mode is for showing the product live. Everything up to and including the
direction choice runs for real — parsing the uploaded call, picking a direction,
proposing topics — because that is the part worth watching. Only two things come
from the recorded job: the first candidate topic (so the run that follows matches
what was written) and the 10-15 minute generation stage itself. The front end is
deliberately not told, so what the audience sees is exactly a real run.

Interactive API docs at /docs.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import time
import traceback
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import markdown as md_lib  # noqa: E402
from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile  # noqa: E402
from fastapi.responses import FileResponse, HTMLResponse  # noqa: E402

from ai4proposal.intake import (  # noqa: E402
    build_task, classify_documents, extract_text, parse_call, parse_template,
    parse_topic_doc, parse_topic_list, pick_direction, propose_topics,
    topics_as_directions,
)
from ai4proposal.llm import LLMBackend, backend_from_env  # noqa: E402

WEB_DIR = Path(__file__).resolve().parent
JOBS_DIR = ROOT / "outputs" / "_web"

app = FastAPI(title="AI4Proposal", docs_url="/docs")

# job_id -> {stage, status, error, title, started}
JOBS: Dict[str, Dict[str, Any]] = {}
JOBS_LOCK = threading.Lock()

STAGES = [
    {"id": "writing", "label": "撰写正文与配图", "hint": "逐章一次成稿，随后生成配图"},
    {"id": "docx", "label": "转换 Word 文档", "hint": "套用中文样式模板"},
    {"id": "review", "label": "多评委评审", "hint": "四位评委 + 主席，含外部文献检索"},
]

SETTINGS: Dict[str, Any] = {"debug": False, "debug_job": None,
                            "demo": False, "demo_dir": None, "demo_pace": 8.0}

# Debug races through the stages because nobody is watching them; demo lingers,
# because the front end polls every 3s and a stage shorter than that can pass
# between two polls and never be drawn.
DEBUG_STAGE_SECONDS = 2.5


# ═══════════════════════════════ helpers ═══════════════════════════════

def _llm() -> LLMBackend:
    key = os.environ.get("AI4PROPOSAL_API_KEY", "").strip()
    if not key:
        raise HTTPException(500, "服务端未配置 AI4PROPOSAL_API_KEY")
    return backend_from_env(key)


def _set(job_id: str, **fields) -> None:
    with JOBS_LOCK:
        JOBS.setdefault(job_id, {}).update(fields)


def _replaying() -> bool:
    return bool(SETTINGS["debug"] or SETTINGS["demo"])


def _resolve_job_dir(spec: str) -> Optional[Path]:
    """Accept a full path, a path relative to the repo, or a bare job name under
    outputs/ — `--demo task_001_v20260901` is what anyone would type first."""
    for cand in (Path(spec), ROOT / spec, ROOT / "outputs" / spec, JOBS_DIR / spec):
        if (cand / "proposal_final.md").exists():
            return cand.resolve()
    return None


def _find_replay_job() -> Optional[Path]:
    """Newest finished job to replay in debug mode."""
    if SETTINGS.get("demo_dir"):
        return Path(SETTINGS["demo_dir"])
    if SETTINGS.get("debug_job"):
        return _resolve_job_dir(SETTINGS["debug_job"])
    candidates = [d for d in JOBS_DIR.glob("*") if (d / "proposal_final.md").exists()]
    if not candidates:
        candidates = [d for d in (ROOT / "outputs").glob("task_*")
                      if (d / "proposal_final.md").exists()]
    return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None


def _demo_topic() -> Optional[Dict[str, Any]]:
    """The recorded job's own topic, shaped like one `propose_topics` returns.

    It carries no marker of its origin: the front end must render it exactly like
    the topics generated beside it. Read from task.json rather than a recorded
    intake payload, because the jobs worth demoing were produced by the CLI and
    never had one.
    """
    job_dir = _find_replay_job()
    if not job_dir:
        return None
    try:
        task = json.loads((job_dir / "task.json").read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"  [warn] 演示课题读取失败：{exc}")
        return None
    if not task.get("title"):
        return None
    return {
        "title": task["title"],
        "domain": task.get("domain") or "general",
        "background": task.get("background") or "",
        "challenges": list(task.get("challenges") or []),
        "fit": "",
    }


def _run_cli(args: List[str], on_line=None) -> subprocess.CompletedProcess:
    """Run a CLI with the running interpreter, reading its output line by line.

    Streaming is what makes progress visible at all. subprocess.run() buffers
    until the process exits, so the writing stage — twelve of the fifteen
    minutes — emitted nothing until it was already over, and the page had a
    blinking dot and a clock to show for it. The pipeline prints a line per
    finished chapter; `on_line` is how that reaches the job state.

    stderr is merged into stdout so the two interleave in run.log in the order
    they were actually written.
    """
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    proc = subprocess.Popen([sys.executable, "-u", *args], cwd=str(ROOT), env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace", bufsize=1)
    lines: List[str] = []
    for line in proc.stdout:                       # type: ignore[union-attr]
        lines.append(line)
        if on_line:
            # A parser bug must not take down a run that is otherwise fine.
            try:
                on_line(line.rstrip("\n"))
            except Exception:  # noqa: BLE001
                pass
    proc.wait()
    return subprocess.CompletedProcess(args, proc.returncode, "".join(lines), "")


# ── progress parsing ────────────────────────────────────────────────────────
# Read off the pipeline's own stdout rather than having it write a status file:
# run_pipeline.py stays a plain CLI that knows nothing about the web app.

_RE_TOTAL   = re.compile(r"\bsections:\s*(\d+)")
_RE_CHAPTER = re.compile(r"^\s*\[OK\]\s+(.+?)\s+\((\d+)字\)\s*$")
_RE_PHASE   = re.compile(r"^\s*\[([1-4])/4\]")
_RE_FIGURE  = re.compile(r"^\s*\[img\]\s+(\S+?):")

_PHASES = {1: "blueprint", 2: "writing", 3: "figures", 4: "judging"}


def _progress_reader(job_id: str, task: Dict[str, Any]):
    """Return an on_line callback that keeps JOBS[job_id]['progress'] current.

    Chapter names for sections not yet written come from the task when it
    declares a structure. When the pipeline plans one at runtime the names are
    only announced in a single comma-joined line, and section names contain
    commas themselves ("… Approach, Timeline, and Milestones"), so pending
    chapters stay unnamed rather than being split wrongly.
    """
    names = [s.get("name", "") for s in
             ((task.get("structure") or {}).get("core_sections") or []) if s.get("name")]
    state: Dict[str, Any] = {
        "phase": "blueprint",
        "total": len(names),
        "names": names,
        "done": [],          # [{name, chars}] in the order they finished
        "figures": 0,
    }
    _set(job_id, progress=dict(state))

    def on_line(line: str) -> None:
        changed = False
        m = _RE_PHASE.match(line)
        if m:
            state["phase"] = _PHASES[int(m.group(1))]
            changed = True
        m = _RE_TOTAL.search(line)
        if m:
            state["total"] = int(m.group(1))
            changed = True
        m = _RE_CHAPTER.match(line)
        if m:
            state["done"].append({"name": m.group(1), "chars": int(m.group(2))})
            changed = True
        if _RE_FIGURE.match(line):
            state["figures"] += 1
            changed = True
        if changed:
            _set(job_id, progress=dict(state))

    return on_line


def _tail(text: str, lines: int = 8) -> str:
    return "\n".join([ln for ln in (text or "").splitlines() if ln.strip()][-lines:])


def _job_dir(job_id: str) -> Path:
    with JOBS_LOCK:
        replay = (JOBS.get(job_id) or {}).get("replay_dir")
    return Path(replay) if replay else JOBS_DIR / job_id


# ═══════════════════════════════ job execution ═══════════════════════════════

def _run_job(job_id: str, task: Dict[str, Any], want_review: bool) -> None:
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    task_path = job_dir / "task.json"
    task_path.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
    log = job_dir / "run.log"

    def append(text: str) -> None:
        with log.open("a", encoding="utf-8") as fh:
            fh.write(text + "\n")

    try:
        _set(job_id, stage="writing", status="running")
        proc = _run_cli(["scripts/run_pipeline.py", "--task", str(task_path), "--out", str(job_dir)],
                        on_line=_progress_reader(job_id, task))
        append(proc.stdout + proc.stderr)
        md = job_dir / "proposal_final.md"
        if proc.returncode != 0 or not md.exists():
            raise RuntimeError(_tail(proc.stdout + proc.stderr) or "生成流水线未产出正文")

        _set(job_id, stage="docx")
        proc = _run_cli(["scripts/md_to_docx.py", str(md)])
        append(proc.stdout + proc.stderr)
        if proc.returncode != 0:
            append("[warn] docx 转换失败，仅提供 Markdown")

        if want_review:
            _set(job_id, stage="review")
            proc = _run_cli(["scripts/evaluate.py", str(md), "--task", str(task_path),
                             "--evidence", "--out", str(job_dir / "evaluation.json")])
            append(proc.stdout + proc.stderr)
            if proc.returncode != 0:
                append("[warn] 评审失败，其余结果仍可用")

        _set(job_id, stage="done", status="done")
    except Exception as exc:  # noqa: BLE001
        append(traceback.format_exc())
        _set(job_id, stage="failed", status="failed", error=str(exc))


def _replay_chapters(replay_dir: Path) -> List[str]:
    """Chapter names in the order /api/job/{id}/section/{index} will serve them.

    That endpoint sorts the directory's .md files by mtime, so the replay has to
    use the same order or a row would open a different chapter than it names.
    Names come from the recorded task.json, which the pipeline writes back with
    the structure it actually used; a file with no matching section falls back to
    its own stem.
    """
    parts = sorted((p for p in replay_dir.glob("*.md") if p.name != "proposal_final.md"),
                   key=lambda p: p.stat().st_mtime)
    names: Dict[str, str] = {}
    try:
        task = json.loads((replay_dir / "task.json").read_text(encoding="utf-8"))
        for s in (task.get("structure") or {}).get("core_sections") or []:
            if s.get("id"):
                names[str(s["id"])] = s.get("name") or str(s["id"])
    except Exception:  # noqa: BLE001
        pass
    return [names.get(p.stem, p.stem) for p in parts]


def _replay_job(job_id: str, replay_dir: Path, want_review: bool,
                dwell: float = DEBUG_STAGE_SECONDS) -> None:
    """Walk the same stages against an already-finished job.

    The writing stage reveals the recorded chapters one at a time, because a
    single dot blinking for the whole stage is exactly what the live progress
    list was built to replace — a demo that skipped it would leave out the part
    worth showing. Each chapter lingers a full `dwell`: the front end polls every
    three seconds in demo mode, and anything shorter lets two or three chapters
    appear between polls in one jump.
    """
    _set(job_id, replay_dir=str(replay_dir))
    try:
        chapters = _replay_chapters(replay_dir)
        drawn = len(list((replay_dir / "figures").glob("*.png")))
        for stage in STAGES:
            if stage["id"] == "review" and not want_review:
                continue
            _set(job_id, stage=stage["id"], status="running")

            if stage["id"] != "writing" or not chapters:
                time.sleep(dwell)
                continue

            state: Dict[str, Any] = {"phase": "writing", "total": len(chapters),
                                     "names": chapters, "done": [], "figures": 0}
            _set(job_id, progress=dict(state))
            for name in chapters:
                time.sleep(dwell)
                state["done"].append({"name": name})
                _set(job_id, progress=dict(state))
            # Figures come after the last chapter in the real pipeline too.
            time.sleep(dwell)
            state["phase"] = "figures"
            state["figures"] = drawn
            _set(job_id, progress=dict(state))

        _set(job_id, stage="done", status="done")
    except Exception as exc:  # noqa: BLE001
        _set(job_id, stage="failed", status="failed", error=str(exc))


def _result_payload(job_dir: Path, fig_base: str,
                    eval_name: str = "evaluation.json") -> Dict[str, Any]:
    """The result the front end renders, from any finished job directory.

    Samples reuse this so the showcase and a real run land on exactly the same
    screen — a sample that rendered through a second, simpler path would drift
    away from the thing it is supposed to be a sample of. `fig_base` is the URL
    prefix its figures are served from; `eval_name` exists because a re-scored
    job keeps its original evaluation.json beside the corrected one.
    """
    md_path = job_dir / "proposal_final.md"
    raw = md_path.read_text(encoding="utf-8") if md_path.exists() else ""

    # Point image tags at the API before rendering, so figures resolve in the page.
    body = raw.replace("](figures/", f"]({fig_base}/")
    # `toc` is here for the ids it stamps on headings, not for the table it can
    # emit: the front end builds its own section navigation and needs anchors to
    # scroll to. An 80,000-character English proposal is unreadable in a plain
    # scroll box.
    html = md_lib.markdown(body, extensions=["tables", "fenced_code", "sane_lists",
                                             "nl2br", "toc"])

    evaluation = None
    ev = job_dir / eval_name
    if ev.exists():
        try:
            evaluation = json.loads(ev.read_text(encoding="utf-8"))
        except Exception:
            pass

    return {
        "html": html,
        "markdown": raw,
        "chars": len(raw),
        "has_docx": (job_dir / "proposal_final.docx").exists(),
        "figures": sorted(p.name for p in (job_dir / "figures").glob("*.png")),
        "evaluation": evaluation,
    }


def _job_result(job_id: str) -> Dict[str, Any]:
    return _result_payload(_job_dir(job_id), f"/api/figure/{job_id}")


# ═══════════════════════════════ samples ═══════════════════════════════
# The six-cell showcase on the landing page. Editorial copy lives in
# web/samples.json; every number comes from the run itself, so re-running a task
# updates the home page without anyone editing HTML.

def _samples() -> List[Dict[str, Any]]:
    manifest = WEB_DIR / "samples.json"
    if not manifest.exists():
        return []
    try:
        entries = json.loads(manifest.read_text(encoding="utf-8")).get("samples", [])
    except Exception as exc:  # noqa: BLE001
        print(f"  [warn] samples.json 解析失败：{exc}")
        return []

    out: List[Dict[str, Any]] = []
    for entry in entries:
        job_dir = (ROOT / entry.get("dir", "")).resolve()
        md = job_dir / "proposal_final.md"
        # outputs/ is not in the repo, so a fresh clone has none of these. Skip
        # quietly; the page hides the section when nothing comes back.
        if not md.exists():
            continue
        try:
            task = json.loads((job_dir / "task.json").read_text(encoding="utf-8"))
        except Exception:
            task = {}
        ev_path = job_dir / entry.get("eval", "evaluation.json")
        score = verdict = None
        if ev_path.exists():
            try:
                ev = json.loads(ev_path.read_text(encoding="utf-8"))
                score, verdict = ev.get("overall_100"), ev.get("verdict")
            except Exception:
                pass
        out.append({
            "id": entry.get("id", ""),
            "tag": entry.get("tag", ""),
            "name": entry.get("name", "") or task.get("program", ""),
            "field": entry.get("field", ""),
            "blurb": entry.get("blurb", ""),
            "title": task.get("title", ""),
            "language": task.get("language", "zh"),
            "figures": len(list((job_dir / "figures").glob("*.png"))),
            "chars": len(md.read_text(encoding="utf-8")),
            "score": score,
            "verdict": verdict,
        })
    return out


def _sample_entry(sample_id: str) -> Optional[Dict[str, Any]]:
    manifest = WEB_DIR / "samples.json"
    if not manifest.exists():
        return None
    try:
        entries = json.loads(manifest.read_text(encoding="utf-8")).get("samples", [])
    except Exception:
        return None
    for entry in entries:
        if entry.get("id") == sample_id:
            job_dir = (ROOT / entry.get("dir", "")).resolve()
            # The id came from our own manifest, but resolve() the path anyway and
            # confirm it stays under outputs/ before reading from it.
            if (job_dir / "proposal_final.md").exists() and \
               (ROOT / "outputs").resolve() in job_dir.parents:
                return {**entry, "path": job_dir}
    return None


# ═══════════════════════════════ routes ═══════════════════════════════

@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return (WEB_DIR / "app.html").read_text(encoding="utf-8")


@app.get("/vendor/{name}")
async def vendor(name: str):
    """Tailwind and Alpine are served locally: cdn.tailwindcss.com is unreachable
    on some networks, and a missing stylesheet leaves the page unreadable."""
    path = (WEB_DIR / "vendor" / Path(name).name).resolve()
    if path.suffix != ".js" or not path.exists():
        raise HTTPException(404, "not found")
    return FileResponse(path, media_type="application/javascript")


_ASSET_TYPES = {".css": "text/css", ".js": "application/javascript"}


@app.get("/{name}.{ext}")
async def asset(name: str, ext: str):
    """The page's own stylesheet and script, split out of app.html.

    Only .css and .js directly under web/ are served, and Path().name strips any
    directory part, so this cannot be walked out of WEB_DIR.
    """
    suffix = f".{ext}"
    if suffix not in _ASSET_TYPES:
        raise HTTPException(404, "not found")
    path = (WEB_DIR / Path(f"{name}{suffix}").name).resolve()
    if not path.exists() or path.parent != WEB_DIR.resolve():
        raise HTTPException(404, "not found")
    return FileResponse(path, media_type=_ASSET_TYPES[suffix])


@app.get("/api/samples")
async def samples() -> Dict[str, Any]:
    """Cards for the landing-page showcase. Empty list is normal on a fresh
    clone — outputs/ is not in the repo."""
    return {"samples": _samples()}


@app.get("/api/sample/{sample_id}")
async def sample(sample_id: str) -> Dict[str, Any]:
    entry = _sample_entry(sample_id)
    if not entry:
        raise HTTPException(404, "示例不存在")
    try:
        task = json.loads((entry["path"] / "task.json").read_text(encoding="utf-8"))
    except Exception:
        task = {}
    return {
        "title": task.get("title", "") or entry.get("name", ""),
        "name": entry.get("name", ""),
        "result": _result_payload(entry["path"], f"/api/sample-figure/{sample_id}",
                                  entry.get("eval", "evaluation.json")),
    }


@app.get("/api/sample-figure/{sample_id}/{name}")
async def sample_figure(sample_id: str, name: str):
    entry = _sample_entry(sample_id)
    if not entry:
        raise HTTPException(404, "示例不存在")
    path = (entry["path"] / "figures" / Path(name).name).resolve()
    if path.suffix != ".png" or not path.exists():
        raise HTTPException(404, "图片不存在")
    return FileResponse(path, media_type="image/png")


@app.get("/api/sample-download/{sample_id}/{kind}")
async def sample_download(sample_id: str, kind: str):
    entry = _sample_entry(sample_id)
    if not entry or kind not in DOWNLOADS:
        raise HTTPException(404, "not found")
    path = entry["path"] / DOWNLOADS[kind]
    if not path.exists():
        raise HTTPException(404, f"{DOWNLOADS[kind]} 不存在")
    return FileResponse(path, filename=path.name,
                        media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream")


@app.get("/api/config")
async def config() -> Dict[str, Any]:
    replay = _find_replay_job() if _replaying() else None
    return {
        # Reported only for --debug. Demo mode deliberately leaves this false so
        # the page renders the badge-free, real-run layout.
        "debug": SETTINGS["debug"],
        "replay_source": replay.name if replay and SETTINGS["debug"] else None,
        "demo": SETTINGS["demo"],
        "demo_source": replay.name if replay and SETTINGS["demo"] else None,
        "stages": STAGES,
        "has_image_key": bool(os.environ.get("AI4PROPOSAL_IMAGE_API_KEY")),
        # Ask the backend rather than re-reading the env with a second default,
        # which would report a model the run does not actually use.
        "model": backend_from_env("unused").model,
    }


@app.post("/api/upload")
async def upload(files: List[UploadFile] = File(default=[]),
                 pasted: str = Form(default="")) -> Dict[str, Any]:
    """Parse uploaded documents and/or text pasted from a call web page.

    Roles are decided from content, not from the user: asking which file is the
    call, which the template and which the topic is work the system can do.
    """
    llm = _llm()
    docs, failed = [], []
    for f in files:
        data = await f.read()
        if not data:
            continue
        try:
            docs.append({"filename": f.filename, "text": extract_text(f.filename, data)})
        except ValueError as exc:
            failed.append({"filename": f.filename, "error": str(exc)})
    if pasted.strip():
        docs.append({"filename": "粘贴的文本", "text": pasted.strip()})

    if not docs:
        raise HTTPException(400, "；".join(f["error"] for f in failed) or "没有可解析的内容")

    # Classification is a convenience, not a precondition: if the model call fails
    # here we still try to parse, treating everything as a call document.
    try:
        roles = classify_documents(llm, docs)
    except Exception as exc:
        print(f"  [warn] 文件分类失败，全部按指南处理：{exc}")
        roles = {}
    out: Dict[str, Any] = {"call": None, "structure": None, "topic": None,
                           "topic_list": None, "files": list(failed)}
    for d in docs:
        role = roles.get(d["filename"], "guideline")
        entry = {"filename": d["filename"], "role": role, "chars": len(d["text"])}
        try:
            if role == "template":
                out["structure"] = parse_template(llm, d["text"])
                entry["sections"] = len((out["structure"] or {}).get("core_sections") or [])
            elif role == "topic_list":
                out["topic_list"] = parse_topic_list(llm, d["text"])
                entry["topics"] = len(out["topic_list"] or [])
            elif role == "topic":
                out["topic"] = parse_topic_doc(llm, d["text"])
            else:
                out["call"] = parse_call(llm, d["text"])
        except ValueError as exc:
            entry["error"] = str(exc)
        except Exception as exc:
            # A timeout, a rejected key or a proxy error must not surface as an
            # opaque 500: the browser cannot even read the body of one.
            entry["error"] = f"解析中断（{type(exc).__name__}）：{str(exc)[:200]}"
        out["files"].append(entry)

    # Nothing parsed at all: report why instead of handing back an empty form.
    if not any(out[k] for k in ("call", "structure", "topic", "topic_list")):
        reasons = "；".join(f["error"] for f in out["files"] if f.get("error"))
        raise HTTPException(502, reasons or "未能从上传内容中解析出任何信息")

    # A template or topic on its own is legitimate — the call may only exist on a
    # web page. Hand back a blank call for the user to fill in; every field is
    # editable anyway, and inventing one here would be worse than leaving it empty.
    if not out["call"]:
        out["call"] = {"program": "", "sponsor": "", "language": "zh",
                       "budget": {"amount": None, "currency": "", "dur": None,
                                  "is_cap": False, "note": ""},
                       "eligibility": "", "requirements": [], "constraints": [],
                       "directions": [{"id": "d1", "name": "（未提供方向，可自行填写）", "detail": ""}],
                       "structure": None,
                       "uncertain": ["未能从上传内容中识别出资助指南，以下字段需自行填写"]}

    # A catalogue of mandated topics IS the set of directions for calls that
    # refuse self-chosen subjects, so it replaces whatever the call enumerated.
    if out["topic_list"]:
        out["call"]["directions"] = topics_as_directions(out["topic_list"])
    return out


@app.post("/api/direction")
async def direction(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    call = payload.get("call") or {}
    if payload.get("mode") == "auto":
        return {"direction": pick_direction(_llm(), call)}
    found = next((d for d in call.get("directions", []) if d.get("id") == payload.get("id")), None)
    if not found:
        raise HTTPException(400, "未找到所选方向")
    return {"direction": found}


@app.post("/api/topics")
async def topics(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Propose candidate topics.

    In demo mode the recorded job's topic leads the list and the model is asked
    for one fewer, so the list is the usual length and choosing the first one
    leads into a generation stage that actually matches it. The rest are really
    generated — that is the part being demonstrated.
    """
    want = int(payload.get("n") or 3)
    seeded = _demo_topic() if SETTINGS["demo"] else None
    if seeded:
        want = max(want - 1, 0)

    generated: List[Dict[str, Any]] = []
    try:
        if want:
            generated = propose_topics(_llm(), payload.get("call") or {},
                                       payload.get("direction") or {},
                                       n=want, guidance=str(payload.get("guidance") or ""))
    except Exception as exc:  # noqa: BLE001
        # A demo must not die on the live half: the recorded topic alone still
        # carries the run forward. Without one, fail as before.
        if not seeded:
            if isinstance(exc, ValueError):
                raise HTTPException(400, str(exc)) from exc
            raise
        print(f"  [warn] 演示模式下候选课题生成失败，仅返回回放课题：{exc}")

    return {"topics": ([seeded] if seeded else []) + generated}


@app.post("/api/run")
async def run(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    topic = payload.get("topic") or {}
    if not topic.get("title"):
        raise HTTPException(400, "缺少课题名称")
    want_review = bool(payload.get("review", True))
    job_id = f"{datetime.now():%m%d-%H%M}-{uuid.uuid4().hex[:6]}"
    task = build_task(payload.get("call") or {}, payload.get("direction") or {}, topic,
                      task_id=job_id, structure=payload.get("structure"))

    _set(job_id, stage="queued", status="running", error="", title=topic["title"],
         started=datetime.now().isoformat(timespec="seconds"), debug=SETTINGS["debug"])

    if _replaying():
        replay = _find_replay_job()
        if not replay:
            raise HTTPException(500, "回放模式下找不到可回放的已完成任务，请先真跑一次")
        dwell = SETTINGS["demo_pace"] if SETTINGS["demo"] else DEBUG_STAGE_SECONDS
        threading.Thread(target=_replay_job, args=(job_id, replay, want_review, dwell),
                         daemon=True).start()
        return {"job_id": job_id, "debug": True, "replay_source": replay.name}

    threading.Thread(target=_run_job, args=(job_id, task, want_review), daemon=True).start()
    return {"job_id": job_id, "debug": False}


@app.get("/api/job/{job_id}")
async def job(job_id: str) -> Dict[str, Any]:
    with JOBS_LOCK:
        state = dict(JOBS.get(job_id) or {})
    if not state:
        raise HTTPException(404, "任务不存在")
    if state.get("status") == "done":
        state["result"] = _job_result(job_id)
    return state


@app.get("/api/job/{job_id}/section/{index}")
async def job_section(job_id: str, index: int) -> Dict[str, Any]:
    """One finished chapter, mid-run.

    The pipeline writes each chapter to its own .md as soon as it is drafted, so
    the wait becomes reading instead of watching a clock. Files are matched by
    write order rather than by section id: when the structure is planned at
    runtime the ids are not known to this process until the run ends and
    task.json is written back.
    """
    job_dir = _job_dir(job_id)
    parts = sorted((p for p in job_dir.glob("*.md") if p.name != "proposal_final.md"),
                   key=lambda p: p.stat().st_mtime)
    if not 0 <= index < len(parts):
        raise HTTPException(404, "章节尚未生成")
    raw = parts[index].read_text(encoding="utf-8")
    return {"html": md_lib.markdown(raw, extensions=["tables", "fenced_code",
                                                     "sane_lists", "nl2br"]),
            "chars": len(raw)}


@app.get("/api/figure/{job_id}/{name}")
async def figure(job_id: str, name: str):
    path = (_job_dir(job_id) / "figures" / Path(name).name).resolve()
    if not path.exists():
        raise HTTPException(404, "图片不存在")
    return FileResponse(path, media_type="image/png")


DOWNLOADS = {"md": "proposal_final.md", "docx": "proposal_final.docx",
             "eval": "evaluation.json", "task": "task.json"}


@app.get("/api/download/{job_id}/{kind}")
async def download(job_id: str, kind: str):
    job_dir = _job_dir(job_id)
    if not job_dir.exists():
        raise HTTPException(404, "任务不存在")
    with JOBS_LOCK:
        title = (JOBS.get(job_id) or {}).get("title") or "proposal"
    stem = "".join(ch for ch in title if ch not in '\\/:*?"<>|')[:40] or "proposal"

    if kind == "zip":
        bundle = JOBS_DIR / f"{job_id}.zip"
        with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as z:
            for p in job_dir.rglob("*"):
                if p.is_file():
                    z.write(p, p.relative_to(job_dir))
        return _attach(bundle, f"{stem}.zip")

    if kind not in DOWNLOADS:
        raise HTTPException(404, "未知的下载类型")
    path = job_dir / DOWNLOADS[kind]
    if not path.exists():
        raise HTTPException(404, "该文件尚未生成")
    return _attach(path, f"{stem}{path.suffix}")


def _attach(path: Path, filename: str) -> FileResponse:
    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=ctype, headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename, safe='')}"})


# ═══════════════════════════════ entry ═══════════════════════════════

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--debug", action="store_true",
                    help="生成阶段回放已完成的任务，不真跑（用于调试前端）")
    ap.add_argument("--debug-job", default="",
                    help="指定回放哪个任务目录，默认取最新完成的一个")
    ap.add_argument("--demo", default="", metavar="任务",
                    help="演示模式：前半段真跑，候选课题首条与正文取自该任务"
                         "（可写 task_001_v20260901 或完整路径）")
    ap.add_argument("--demo-pace", type=float, default=SETTINGS["demo_pace"],
                    help="演示模式下每个阶段、以及撰写阶段每一章的停留秒数")
    ap.add_argument("--reload", action="store_true", help="改动代码后自动重启")
    args = ap.parse_args()

    SETTINGS["debug"] = args.debug
    SETTINGS["debug_job"] = args.debug_job or None
    SETTINGS["demo"] = bool(args.demo)
    SETTINGS["demo_pace"] = args.demo_pace
    JOBS_DIR.mkdir(parents=True, exist_ok=True)

    if args.demo:
        # Refuse rather than silently fall through to a real 10-15 minute run:
        # discovering that mid-demo is the whole failure this mode exists to avoid.
        demo_dir = _resolve_job_dir(args.demo)
        if not demo_dir:
            print(f"  [错误] 找不到可回放的任务 {args.demo}（需含 proposal_final.md）")
            return 1
        SETTINGS["demo_dir"] = str(demo_dir)

    if not os.environ.get("AI4PROPOSAL_API_KEY"):
        print("  [warn] 未设置 AI4PROPOSAL_API_KEY，解析与生成会失败")
    if not os.environ.get("AI4PROPOSAL_IMAGE_API_KEY") and not args.demo:
        print("  [warn] 未设置 AI4PROPOSAL_IMAGE_API_KEY，将不生成配图")
    if args.debug:
        replay = _find_replay_job()
        print(f"  [调试模式] 生成阶段将回放：{replay if replay else '（未找到可回放任务）'}")
    if args.demo:
        topic = _demo_topic()
        print(f"  [演示模式] 回放任务：{SETTINGS['demo_dir']}")
        print(f"             首条候选课题：{topic['title'] if topic else '（task.json 无课题，仅回放正文）'}")
        print("             上传解析、选择方向、其余候选课题均为真跑")

    import uvicorn
    print(f"\n  AI4Proposal  →  http://{args.host}:{args.port}      API 文档 /docs\n")
    uvicorn.run("web.server:app" if args.reload else app, host=args.host, port=args.port,
                reload=args.reload, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
