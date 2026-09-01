#!/usr/bin/env python3
"""FastAPI server for the AI4Proposal agent.

Jobs shell out to the existing CLIs with the running interpreter rather than
importing them, so the pipeline stays free to change and a crashing job cannot
take the server down.

    uv run python web/server.py                 # http://127.0.0.1:8000
    uv run python web/server.py --debug         # 生成阶段回放已有产物，秒出结果

Debug mode exists because a real run takes 10-15 minutes, which makes iterating
on the front end impossible. It replays a completed job through the same stage
progression and the same result payload, so every screen after "开始生成" is
exercised without spending a single token. The UI shows a badge whenever it is
on, so a replay is never mistaken for a real run.

Interactive API docs at /docs.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
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

SETTINGS: Dict[str, Any] = {"debug": False, "debug_job": None}


# ═══════════════════════════════ helpers ═══════════════════════════════

def _llm() -> LLMBackend:
    key = os.environ.get("AI4PROPOSAL_API_KEY", "").strip()
    if not key:
        raise HTTPException(500, "服务端未配置 AI4PROPOSAL_API_KEY")
    return backend_from_env(key)


def _set(job_id: str, **fields) -> None:
    with JOBS_LOCK:
        JOBS.setdefault(job_id, {}).update(fields)


def _find_replay_job() -> Optional[Path]:
    """Newest finished job to replay in debug mode."""
    if SETTINGS.get("debug_job"):
        p = Path(SETTINGS["debug_job"])
        return p if (p / "proposal_final.md").exists() else None
    candidates = [d for d in JOBS_DIR.glob("*") if (d / "proposal_final.md").exists()]
    if not candidates:
        candidates = [d for d in (ROOT / "outputs").glob("task_*")
                      if (d / "proposal_final.md").exists()]
    return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None


def _run_cli(args: List[str]) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    return subprocess.run([sys.executable, "-u", *args], cwd=str(ROOT), env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


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
        proc = _run_cli(["scripts/run_pipeline.py", "--task", str(task_path), "--out", str(job_dir)])
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


def _replay_job(job_id: str, replay_dir: Path, want_review: bool) -> None:
    """Walk the same stages against an already-finished job, a few seconds each."""
    _set(job_id, replay_dir=str(replay_dir))
    try:
        for stage in STAGES:
            if stage["id"] == "review" and not want_review:
                continue
            _set(job_id, stage=stage["id"], status="running")
            time.sleep(2.5)
        _set(job_id, stage="done", status="done")
    except Exception as exc:  # noqa: BLE001
        _set(job_id, stage="failed", status="failed", error=str(exc))


def _job_result(job_id: str) -> Dict[str, Any]:
    job_dir = _job_dir(job_id)
    md_path = job_dir / "proposal_final.md"
    raw = md_path.read_text(encoding="utf-8") if md_path.exists() else ""

    # Point image tags at the API before rendering, so figures resolve in the page.
    body = raw.replace("](figures/", f"](/api/figure/{job_id}/")
    html = md_lib.markdown(body, extensions=["tables", "fenced_code", "sane_lists", "nl2br"])

    evaluation = None
    ev = job_dir / "evaluation.json"
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


@app.get("/api/config")
async def config() -> Dict[str, Any]:
    replay = _find_replay_job() if SETTINGS["debug"] else None
    return {
        "debug": SETTINGS["debug"],
        "replay_source": replay.name if replay else None,
        "stages": STAGES,
        "has_image_key": bool(os.environ.get("AI4PROPOSAL_IMAGE_API_KEY")),
        "model": os.environ.get("AI4PROPOSAL_MODEL", "deepseek-chat"),
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
    try:
        return {"topics": propose_topics(_llm(), payload.get("call") or {},
                                         payload.get("direction") or {},
                                         n=int(payload.get("n") or 3),
                                         guidance=str(payload.get("guidance") or ""))}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


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

    if SETTINGS["debug"]:
        replay = _find_replay_job()
        if not replay:
            raise HTTPException(500, "调试模式下找不到可回放的已完成任务，请先真跑一次")
        threading.Thread(target=_replay_job, args=(job_id, replay, want_review), daemon=True).start()
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
    ap.add_argument("--reload", action="store_true", help="改动代码后自动重启")
    args = ap.parse_args()

    SETTINGS["debug"] = args.debug
    SETTINGS["debug_job"] = args.debug_job or None
    JOBS_DIR.mkdir(parents=True, exist_ok=True)

    if not os.environ.get("AI4PROPOSAL_API_KEY"):
        print("  [warn] 未设置 AI4PROPOSAL_API_KEY，解析与生成会失败")
    if not os.environ.get("AI4PROPOSAL_IMAGE_API_KEY"):
        print("  [warn] 未设置 AI4PROPOSAL_IMAGE_API_KEY，将不生成配图")
    if args.debug:
        replay = _find_replay_job()
        print(f"  [调试模式] 生成阶段将回放：{replay if replay else '（未找到可回放任务）'}")

    import uvicorn
    print(f"\n  AI4Proposal  →  http://{args.host}:{args.port}      API 文档 /docs\n")
    uvicorn.run("web.server:app" if args.reload else app, host=args.host, port=args.port,
                reload=args.reload, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
