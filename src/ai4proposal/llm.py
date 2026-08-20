from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional


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

    @classmethod
    def from_env(cls) -> Optional["LLMBackend"]:
        enabled = os.getenv("AI4PROPOSAL_USE_LLM", "").strip().lower()
        if enabled not in {"1", "true", "yes", "on"}:
            return None
        model = os.getenv("AI4PROPOSAL_MODEL", "").strip()
        if not model:
            raise ValueError("AI4PROPOSAL_USE_LLM is enabled but AI4PROPOSAL_MODEL is not set.")
        api_key = (
            os.getenv("AI4PROPOSAL_API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or os.getenv("CUSTOM_API_KEY")
            or ""
        ).strip()
        if not api_key:
            raise ValueError("No API key found for AI4Proposal LLM mode.")
        base_url = (
            os.getenv("AI4PROPOSAL_BASE_URL")
            or os.getenv("OPENAI_BASE_URL")
            or os.getenv("CUSTOM_BASE_URL")
            or ""
        ).strip() or None
        timeout_seconds = float(os.getenv("AI4PROPOSAL_TIMEOUT_SECONDS", "300"))
        return cls(
            model=model,
            api_key=api_key,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            max_retries=int(os.getenv("AI4PROPOSAL_SDK_RETRIES", "1")),
        )

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

        t0 = time.time()
        try:
            completion = client.chat.completions.create(model=self.model, messages=messages)
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
            response = client.responses.create(model=self.model, input=messages)
            text = _extract_response_text(response)
        except Exception as exc:
            self._trace("responses", t1, "", f"{type(exc).__name__}: {exc}")
            raise
        self._trace("responses", t1, text)
        return text


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
    )


def generate_with_retry(llm: Optional[LLMBackend], system: str, prompt: str,
                        fallback: str = "", attempts: int = 4) -> str:
    """Call the backend with linear backoff; return `fallback` if every try fails."""
    if llm is None:
        return fallback
    for attempt in range(attempts):
        try:
            return llm.generate_text(system_prompt=system, user_prompt=prompt)
        except Exception as e:
            print(f"    [retry {attempt + 1}: {str(e)[:100]}]")
            if attempt < attempts - 1:
                time.sleep((attempt + 1) * 10)
    return fallback


def parse_json(text: str) -> Dict[str, Any]:
    """Best-effort JSON extraction from an LLM reply (tolerates prose/fences)."""
    try:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(text[start:end])
    except Exception:
        pass
    return {}
