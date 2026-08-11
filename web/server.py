#!/usr/bin/env python3
"""Local web server for the AI4Proposal agent.

Wraps the existing CLIs rather than importing them: a job shells out to
scripts/run_pipeline.py, scripts/md_to_docx.py and scripts/evaluate.py with the
interpreter currently running this server. That keeps the pipeline free to
change without the web layer tracking its internals, and means a crash in a job
cannot take the server down with it.

Stdlib only — no framework. Uploads arrive as base64 inside a JSON body rather
than multipart/form-data, because Python 3.13 removed the `cgi` module and
parsing multipart by hand is not worth it for a local tool.

    uv run python web/server.py            # http://127.0.0.1:8000
    uv run python web/server.py --port 8080

Env: the same variables the CLIs use (AI4PROPOSAL_API_KEY / _BASE_URL / _MODEL,
AI4PROPOSAL_IMAGE_API_KEY / _BASE_URL for figures).
"""
from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import shutil
import subprocess
import sys
import threading
import traceback
import uuid
import zipfile
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ai4proposal.intake import (  # noqa: E402
    build_task, extract_text, parse_call, pick_direction, propose_topics,
)
from ai4proposal.llm import LLMBackend  # noqa: E402

WEB_DIR = Path(__file__).resolve().parent
JOBS_DIR = ROOT / "outputs" / "_web"
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

# job_id -> {stage, status, error, dir, title, started}
JOBS: Dict[str, Dict[str, Any]] = {}
JOBS_LOCK = threading.Lock()

STAGES = [
    ("writing", "撰写申请书正文与配图"),
    ("docx", "转换为 Word 文档"),
    ("review", "多评委评审与外部检索"),
]


def _llm() -> LLMBackend:
    key = os.environ.get("AI4PROPOSAL_API_KEY", "").strip()
    if not key:
        raise RuntimeError("服务端未配置 AI4PROPOSAL_API_KEY，无法调用模型")
    return LLMBackend(
        model=os.environ.get("AI4PROPOSAL_MODEL", "deepseek-chat"),
        api_key=key,
        base_url=os.environ.get("AI4PROPOSAL_BASE_URL", "https://api.deepseek.com"),
        timeout_seconds=float(os.environ.get("AI4PROPOSAL_TIMEOUT_SECONDS", "180")),
    )


# ═══════════════════════════════ job execution ═══════════════════════════════

def _set(job_id: str, **fields) -> None:
    with JOBS_LOCK:
        JOBS.setdefault(job_id, {}).update(fields)


def _run_cli(args: list, cwd: Path) -> subprocess.CompletedProcess:
    """Run one of the project CLIs with this server's interpreter."""
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    return subprocess.run([sys.executable, "-u", *args], cwd=str(cwd), env=env,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace")


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
        proc = _run_cli(
            ["scripts/run_pipeline.py", "--task", str(task_path), "--out", str(job_dir)], ROOT)
        append(proc.stdout + proc.stderr)
        md = job_dir / "proposal_final.md"
        if proc.returncode != 0 or not md.exists():
            raise RuntimeError(_tail(proc.stdout + proc.stderr) or "生成流水线未产出正文")

        _set(job_id, stage="docx")
        proc = _run_cli(["scripts/md_to_docx.py", str(md)], ROOT)
        append(proc.stdout + proc.stderr)
        # A failed Word conversion should not lose the proposal — record and continue.
        if proc.returncode != 0:
            append("[warn] docx 转换失败，仅提供 Markdown")

        if want_review:
            _set(job_id, stage="review")
            proc = _run_cli(["scripts/evaluate.py", str(md), "--task", str(task_path),
                             "--evidence", "--out", str(job_dir / "evaluation.json")], ROOT)
            append(proc.stdout + proc.stderr)
            if proc.returncode != 0:
                append("[warn] 评审失败，其余结果仍可用")

        _set(job_id, stage="done", status="done")
    except Exception as exc:  # noqa: BLE001 — surface any failure to the browser
        append(traceback.format_exc())
        _set(job_id, stage="failed", status="failed", error=str(exc))


def _tail(text: str, lines: int = 6) -> str:
    kept = [ln for ln in (text or "").splitlines() if ln.strip()][-lines:]
    return "\n".join(kept)


def _job_result(job_id: str) -> Dict[str, Any]:
    job_dir = JOBS_DIR / job_id
    md = job_dir / "proposal_final.md"
    out: Dict[str, Any] = {
        "markdown": md.read_text(encoding="utf-8") if md.exists() else "",
        "has_docx": (job_dir / "proposal_final.docx").exists(),
        "figures": sorted(p.name for p in (job_dir / "figures").glob("*.png")),
        "evaluation": None,
    }
    ev = job_dir / "evaluation.json"
    if ev.exists():
        try:
            out["evaluation"] = json.loads(ev.read_text(encoding="utf-8"))
        except Exception:
            pass
    return out


# ═══════════════════════════════ HTTP ═══════════════════════════════

class Handler(BaseHTTPRequestHandler):
    server_version = "AI4Proposal"

    def log_message(self, fmt, *args):  # quieter console
        if "/api/job/" not in (self.path or ""):
            sys.stderr.write(f"  {self.command} {self.path}\n")

    # ── plumbing ──
    def _send(self, code: int, body: bytes, ctype: str, extra: Optional[dict] = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj: Any, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _fail(self, message: str, code: int = 400) -> None:
        self._json({"error": message}, code)

    def _body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > MAX_UPLOAD_BYTES:
            raise ValueError(f"请求过大（上限 {MAX_UPLOAD_BYTES // 1024 // 1024} MB）")
        return json.loads(self.rfile.read(length).decode("utf-8"))

    # ── routes ──
    def do_GET(self) -> None:  # noqa: N802
        path = unquote(urlparse(self.path).path)
        try:
            if path in ("/", "/index.html"):
                return self._file(WEB_DIR / "app.html", "text/html; charset=utf-8")
            if path.startswith("/api/job/"):
                return self._get_job(path.rsplit("/", 1)[-1])
            if path.startswith("/api/figure/"):
                job_id, _, name = path[len("/api/figure/"):].partition("/")
                return self._figure(job_id, name)
            if path.startswith("/api/download/"):
                parts = path[len("/api/download/"):].split("/")
                if len(parts) == 2:
                    return self._download(parts[0], parts[1])
            self._fail("Not found", 404)
        except Exception as exc:  # noqa: BLE001
            self._fail(str(exc), 500)

    def do_POST(self) -> None:  # noqa: N802
        path = unquote(urlparse(self.path).path)
        try:
            body = self._body()
            if path == "/api/parse":
                return self._parse(body)
            if path == "/api/direction":
                return self._direction(body)
            if path == "/api/topics":
                return self._topics(body)
            if path == "/api/run":
                return self._start(body)
            self._fail("Not found", 404)
        except ValueError as exc:
            self._fail(str(exc), 400)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self._fail(str(exc), 500)

    # ── handlers ──
    def _file(self, path: Path, ctype: str) -> None:
        if not path.exists():
            return self._fail("Not found", 404)
        self._send(200, path.read_bytes(), ctype)

    def _parse(self, body: Dict[str, Any]) -> None:
        name = body.get("filename") or ""
        raw = body.get("data_b64") or ""
        if not raw:
            raise ValueError("未收到文件内容")
        data = base64.b64decode(raw)
        text = extract_text(name, data)          # raises ValueError with a readable message
        call = parse_call(_llm(), text)
        self._json({"call": call, "chars": len(text)})

    def _direction(self, body: Dict[str, Any]) -> None:
        call = body.get("call") or {}
        if body.get("mode") == "auto":
            return self._json({"direction": pick_direction(_llm(), call)})
        wanted = body.get("id")
        found = next((d for d in call.get("directions", []) if d.get("id") == wanted), None)
        if not found:
            raise ValueError("未找到所选方向")
        self._json({"direction": found})

    def _topics(self, body: Dict[str, Any]) -> None:
        topics = propose_topics(_llm(), body.get("call") or {},
                                body.get("direction") or {}, n=int(body.get("n") or 3))
        self._json({"topics": topics})

    def _start(self, body: Dict[str, Any]) -> None:
        call = body.get("call") or {}
        direction = body.get("direction") or {}
        topic = body.get("topic") or {}
        if not topic.get("title"):
            raise ValueError("缺少课题名称")
        job_id = f"{datetime.now():%m%d-%H%M}-{uuid.uuid4().hex[:6]}"
        task = build_task(call, direction, topic, task_id=job_id)
        _set(job_id, stage="queued", status="running", error="",
             title=topic["title"], started=datetime.now().isoformat(timespec="seconds"))
        threading.Thread(target=_run_job, args=(job_id, task, bool(body.get("review", True))),
                         daemon=True).start()
        self._json({"job_id": job_id, "stages": [{"id": s, "label": l} for s, l in STAGES]})

    def _get_job(self, job_id: str) -> None:
        with JOBS_LOCK:
            job = dict(JOBS.get(job_id) or {})
        if not job:
            return self._fail("任务不存在", 404)
        if job.get("status") == "done":
            job["result"] = _job_result(job_id)
        self._json(job)

    def _figure(self, job_id: str, name: str) -> None:
        path = (JOBS_DIR / job_id / "figures" / Path(name).name).resolve()
        if not str(path).startswith(str(JOBS_DIR.resolve())) or not path.exists():
            return self._fail("Not found", 404)
        self._send(200, path.read_bytes(), "image/png")

    def _download(self, job_id: str, kind: str) -> None:
        job_dir = (JOBS_DIR / job_id).resolve()
        if not str(job_dir).startswith(str(JOBS_DIR.resolve())) or not job_dir.exists():
            return self._fail("Not found", 404)
        title = (JOBS.get(job_id) or {}).get("title") or "proposal"
        stem = "".join(ch for ch in title if ch not in '\\/:*?"<>|')[:40] or "proposal"

        if kind == "zip":
            buf = job_dir / "_bundle.zip"
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                for p in job_dir.rglob("*"):
                    if p.is_file() and p.name != "_bundle.zip":
                        z.write(p, p.relative_to(job_dir))
            return self._attach(buf, f"{stem}.zip")

        names = {"md": "proposal_final.md", "docx": "proposal_final.docx",
                 "eval": "evaluation.json", "task": "task.json"}
        if kind not in names:
            return self._fail("未知的下载类型", 404)
        path = job_dir / names[kind]
        if not path.exists():
            return self._fail("该文件尚未生成", 404)
        self._attach(path, f"{stem}{path.suffix}")

    def _attach(self, path: Path, filename: str) -> None:
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        quoted = filename.encode("utf-8").decode("latin-1", errors="ignore")
        self._send(200, path.read_bytes(), ctype,
                   {"Content-Disposition": f"attachment; filename*=UTF-8''{_pct(filename)}",
                    "X-Filename": quoted})


def _pct(text: str) -> str:
    from urllib.parse import quote
    return quote(text, safe="")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    missing = [k for k in ("AI4PROPOSAL_API_KEY",) if not os.environ.get(k)]
    if missing:
        print(f"  [warn] 未设置 {', '.join(missing)}，解析与生成会失败")
    if not os.environ.get("AI4PROPOSAL_IMAGE_API_KEY"):
        print("  [warn] 未设置 AI4PROPOSAL_IMAGE_API_KEY，将不生成配图")

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"\n  AI4Proposal  →  http://{args.host}:{args.port}\n  Ctrl+C 停止\n")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  已停止")
    return 0


if __name__ == "__main__":
    sys.exit(main())
