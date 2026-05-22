from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Optional


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


@dataclass
class LLMBackend:
    model: str
    api_key: str
    base_url: Optional[str] = None
    timeout_seconds: float = 90.0

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
        timeout_seconds = float(os.getenv("AI4PROPOSAL_TIMEOUT_SECONDS", "90"))
        return cls(
            model=model,
            api_key=api_key,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
        )

    def _make_client(self) -> Any:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("openai package is required for AI4Proposal LLM mode.") from exc
        return OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.timeout_seconds,
        )

    def generate_text(self, system_prompt: str, user_prompt: str) -> str:
        client = self._make_client()
        try:
            response = client.responses.create(
                model=self.model,
                input=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            text = _extract_response_text(response)
            if text:
                return text
        except Exception:
            pass

        completion = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        message = completion.choices[0].message.content
        if isinstance(message, list):
            parts = []
            for block in message:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
            return "\n".join(part.strip() for part in parts if part and part.strip()).strip()
        return str(message).strip()
