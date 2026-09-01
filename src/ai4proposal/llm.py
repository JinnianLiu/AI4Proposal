from __future__ import annotations

import base64
import json
import mimetypes
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union


def _extract_response_text(response: Any) -> str:
    if isinstance(response, str):
        return response.strip()
    if isinstance(response, bytes):
        return response.decode("utf-8", errors="ignore").strip()
    output_text = getattr(response, "output_text", None)
    if output_text:
        return str(output_text).strip()

    pieces = []
    for item in getattr(response, "output", []) or []:
        for content in getattr(item, "content", []) or []:
            text = getattr(content, "text", None)
            if text:
                pieces.append(str(text))
    return "\n".join(piece.strip() for piece in pieces if piece and piece.strip()).strip()


class CallDeadlineExceeded(TimeoutError):
    """A single HTTP call outran its wall-clock budget."""


def _with_deadline(fn, seconds: float, label: str):
    """Run `fn` under a wall-clock deadline.

    The SDK's `timeout` is httpx's, which is an *idle* timeout — it resets on
    every byte received. A backend that dribbles a response therefore never
    trips it: one observed chat.completions call ran 2041s and returned
    normally under a 300s setting. Nothing can cancel the in-flight request, so
    the worker is a daemon and is simply abandoned; the caller retries.
    """
    box: Dict[str, Any] = {}

    def run() -> None:
        try:
            box["ok"] = fn()
        except BaseException as exc:            # noqa: BLE001 - re-raised below
            box["err"] = exc

    worker = threading.Thread(target=run, name=f"llm-{label}", daemon=True)
    worker.start()
    worker.join(seconds)
    if worker.is_alive():
        raise CallDeadlineExceeded(f"{label} exceeded {seconds:.0f}s wall clock")
    if "err" in box:
        raise box["err"]
    return box.get("ok")


def _message_text(message: Any) -> str:
    """Text out of a chat.completions message, which may be a string or blocks."""
    if isinstance(message, list):
        parts = [b.get("text", "") for b in message
                 if isinstance(b, dict) and b.get("type") == "text"]
        return "\n".join(p.strip() for p in parts if p and p.strip()).strip()
    return str(message or "").strip()


@dataclass
class LLMBackend:
    model: str
    api_key: str
    base_url: Optional[str] = None
    timeout_seconds: float = 300.0
    max_retries: int = 1
    # Wall-clock ceiling per HTTP call, enforced above the SDK's idle timeout.
    deadline_seconds: float = 600.0

    def _make_client(self) -> Any:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("openai package is required for AI4Proposal LLM mode.") from exc
        # max_retries is set explicitly: the SDK's default of 2 means one visible
        # call can silently become three requests, so a stalled run shows no trace
        # of where its time went.
        return OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.timeout_seconds,
            max_retries=self.max_retries,
        )

    def _trace(self, endpoint: str, t0: float, text: str, error: str = "") -> None:
        """One line per HTTP call. Without it a slow run is unattributable: a
        30-minute gap in the log looked identical to a fast call, because the
        endpoint fallback swallowed its own failures."""
        if os.getenv("AI4PROPOSAL_QUIET_LLM", "").strip().lower() in {"1", "true", "yes", "on"}:
            return
        took = time.time() - t0
        tail = f"FAILED {error[:120]}" if error else f"{len(text)}字"
        print(f"    [llm] {endpoint} {took:.1f}s {tail}", flush=True)

    def generate_text(self, system_prompt: str, user_prompt: str) -> str:
        """Prefer chat.completions; fall back to /responses.

        The order used to be the other way round. Both work on the DeepSeek
        endpoint, so the fallback never ran — and on a 4000-word chapter
        /responses measured 206s against 92s for chat.completions, so every call
        in the pipeline paid roughly double for nothing.
        """
        client = self._make_client()
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        deadline = self.deadline_seconds

        t0 = time.time()
        try:
            completion = _with_deadline(
                lambda: client.chat.completions.create(model=self.model, messages=messages),
                deadline, "chat")
            text = _message_text(completion.choices[0].message.content)
            if text:
                self._trace("chat", t0, text)
                return text
            why = "empty reply"
        except Exception as exc:
            why = f"{type(exc).__name__}: {exc}"
        self._trace("chat", t0, "", why)

        t1 = time.time()
        try:
            response = _with_deadline(
                lambda: client.responses.create(model=self.model, input=messages),
                deadline, "responses")
            text = _extract_response_text(response)
        except Exception as exc:
            self._trace("responses", t1, "", f"{type(exc).__name__}: {exc}")
            raise
        self._trace("responses", t1, text)
        return text

    def generate_with_images(self, system_prompt: str, user_prompt: str,
                             images: Sequence[Union[str, Path]]) -> str:
        """Same contract as generate_text, with images attached to the user turn.

        Images are inlined as data URIs rather than URLs: the figures live on
        local disk and there is nothing to serve them from. chat.completions
        only — the fallback endpoint is not in play for a vision model.
        """
        client = self._make_client()
        parts: List[Dict[str, Any]] = [{"type": "text", "text": user_prompt}]
        for path in images:
            p = Path(path)
            mime = mimetypes.guess_type(p.name)[0] or "image/png"
            b64 = base64.b64encode(p.read_bytes()).decode()
            parts.append({"type": "image_url",
                          "image_url": {"url": f"data:{mime};base64,{b64}"}})

        t0 = time.time()
        try:
            completion = _with_deadline(
                lambda: client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "system", "content": system_prompt},
                              {"role": "user", "content": parts}]),
                self.deadline_seconds, "vision")
            text = _message_text(completion.choices[0].message.content)
        except Exception as exc:
            self._trace("vision", t0, "", f"{type(exc).__name__}: {exc}")
            raise
        self._trace("vision", t0, text)
        return text


def backend_from_env(api_key: Optional[str] = None) -> LLMBackend:
    """The text backend every entry point uses, built from the documented env vars.

    Each of run_pipeline / evaluate / measure_variance / web.server used to inline
    this same block, so a changed default (the 300s timeout, the SDK_RETRIES=1 that
    stops one visible call becoming three requests) had to be edited in four
    places. `api_key` is a parameter because the web app can take the key from a
    request instead of the environment.
    """
    return LLMBackend(
        model=os.environ.get("AI4PROPOSAL_MODEL", "deepseek-chat"),
        api_key=api_key if api_key is not None else os.environ.get("AI4PROPOSAL_API_KEY", ""),
        base_url=os.environ.get("AI4PROPOSAL_BASE_URL", "https://api.deepseek.com"),
        timeout_seconds=float(os.environ.get("AI4PROPOSAL_TIMEOUT_SECONDS", "300")),
        max_retries=int(os.environ.get("AI4PROPOSAL_SDK_RETRIES", "1")),
        deadline_seconds=float(os.environ.get("AI4PROPOSAL_CALL_DEADLINE_SECONDS", "600")),
    )


def cheap_backend(main: LLMBackend) -> LLMBackend:
    """A smaller/faster model on the same endpoint, for auxiliary calls such as
    evidence reranking where judgement quality is not the bottleneck.
    Override with AI4PROPOSAL_CHEAP_MODEL."""
    return LLMBackend(
        model=os.getenv("AI4PROPOSAL_CHEAP_MODEL", "deepseek-v4-flash").strip() or main.model,
        api_key=main.api_key,
        base_url=main.base_url,
        timeout_seconds=main.timeout_seconds,
        max_retries=main.max_retries,
        deadline_seconds=main.deadline_seconds,
    )


def vision_backend(main: LLMBackend) -> LLMBackend:
    """A multimodal model on the same endpoint, for looking at the figures the
    text judges cannot see. Override with AI4PROPOSAL_VISION_MODEL; set it empty
    to turn figure review off."""
    model = os.getenv("AI4PROPOSAL_VISION_MODEL", "deepseek-v4-flash-vision-exp").strip()
    if not model:
        raise ValueError("AI4PROPOSAL_VISION_MODEL is empty: figure review disabled")
    return LLMBackend(
        model=model,
        api_key=main.api_key,
        base_url=main.base_url,
        timeout_seconds=main.timeout_seconds,
        max_retries=main.max_retries,
        deadline_seconds=main.deadline_seconds,
    )


def generate_with_retry(llm: Optional[LLMBackend], system: str, prompt: str,
                        fallback: str = "", attempts: int = 4) -> str:
    """Call the backend with linear backoff; return `fallback` if every try fails.

    Retrying a call that died on its wall-clock deadline could cost more than
    the stall it was meant to cut (4 x 600s beats the 2041s stall we saw), so
    the whole sequence is bounded by twice one deadline. Fast failures — a 429,
    a bad key — barely consume that budget and still get all their attempts.
    """
    if llm is None:
        return fallback
    budget = llm.deadline_seconds * 2
    spent = 0.0                     # time inside calls only; backoff sleeps are
    for attempt in range(attempts):  # deliberately excluded from the budget
        t0 = time.time()
        try:
            return llm.generate_text(system_prompt=system, user_prompt=prompt)
        except Exception as e:
            spent += time.time() - t0
            print(f"    [retry {attempt + 1}: {str(e)[:100]}]")
            if spent > budget:
                print(f"    [retry] 放弃：调用累计 {spent:.0f}s 超出 {budget:.0f}s 预算")
                break
            if attempt < attempts - 1 and not isinstance(e, CallDeadlineExceeded):
                time.sleep((attempt + 1) * 10)
    return fallback


def parse_json(text: str) -> Dict[str, Any]:
    """Best-effort JSON extraction from an LLM reply (tolerates prose/fences).

    The one extractor for the whole package. There used to be five near-copies
    (here, evaluation, evidence, intake, image_gen) that had quietly drifted
    apart — only two guarded against empty input, only one checked that the
    parse actually produced an object — so how tolerant a judge was of a
    malformed reply depended on which module happened to call it.
    """
    if not text:
        return {}
    try:
        start, end = text.find("{"), text.rfind("}") + 1
        if start < 0 or end <= start:
            return {}
        parsed = json.loads(text[start:end])
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}
