"""Image generation for proposal figures via an OpenAI-compatible images API.

The pipeline emits [figure: <chinese description>] markers. This module turns each
marker into a real PNG by calling an images/generations endpoint (e.g. gpt-image-2
behind an OpenAI-compatible proxy). Failures degrade gracefully: the caller keeps a
text placeholder if generation returns None.

No keys are hardcoded — everything comes from arguments / environment.
"""
from __future__ import annotations

import base64
import json
import os
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

# Some corporate/proxy chains break cert validation; mirror evidence.py's context.
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

# Wrap the raw (often Chinese) figure description so the model draws a clean,
# proposal-grade schematic instead of a photo, and keeps text to a minimum.
_STYLE_PREFIX = (
    "A detailed, information-dense technical figure for a research grant proposal, "
    "in the visual style of a top-tier computer-science conference paper figure. "
    "Compose MULTIPLE labeled sub-panels or layered/stacked module blocks connected "
    "by arrows that show data flow — this is an architecture / pipeline / comparison / "
    "worked-example figure, NOT a single icon, NOT a logo, NOT a metaphor illustration. "
    "Include, where relevant: labeled component boxes, small schematic plots or matrices, "
    "concrete example thumbnails, and baseline-vs-ours comparisons. "
    "Clean flat vector style, professional academic look, white background, thin clear "
    "connectors, short English labels, soft color-coded panels, no photorealism, no watermark. "
    "Figure content: "
)


def build_image_prompt(desc: str, style_prefix: bool = True) -> str:
    """The exact string sent to the image model = style prefix + the figure description."""
    return (_STYLE_PREFIX + desc) if style_prefix else desc


def _endpoint(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/images/generations"):
        return base
    if base.endswith("/v1"):
        return base + "/images/generations"
    return base + "/v1/images/generations"


def generate_image(
    prompt: str,
    out_path: Path,
    api_key: str,
    base_url: str = "https://api.chatanywhere.tech/v1",
    model: str = "gpt-image-2",
    size: str = "1024x1024",
    timeout: float = 180.0,
    max_retries: int = 2,
    style_prefix: bool = True,
) -> Optional[Path]:
    """Generate one image and save it as PNG. Returns the path, or None on failure."""
    if not api_key:
        return None
    full_prompt = build_image_prompt(prompt, style_prefix)
    url = _endpoint(base_url)
    body = json.dumps({"model": model, "prompt": full_prompt, "n": 1, "size": size}).encode()
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    data = None
    for attempt in range(max_retries + 1):
        try:
            req = urllib.request.Request(url, data=body, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
                data = json.loads(resp.read().decode())
            break
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < max_retries:
                time.sleep(2 ** attempt * 3)
                continue
            return None
        except Exception:
            if attempt < max_retries:
                time.sleep(2 ** attempt * 2)
                continue
            return None
    if not data:
        return None

    try:
        item = (data.get("data") or [{}])[0]
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if item.get("b64_json"):
            out_path.write_bytes(base64.b64decode(item["b64_json"]))
            return out_path
        if item.get("url"):
            img_url = item["url"]
            req = urllib.request.Request(img_url, headers={"User-Agent": "AI4Proposal/1.0"})
            with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
                out_path.write_bytes(resp.read())
            return out_path
    except Exception:
        return None
    return None


def image_config_from_env() -> dict:
    """Read image-gen settings from env (keeps keys out of source)."""
    return {
        "api_key": os.environ.get("AI4PROPOSAL_IMAGE_API_KEY", ""),
        "base_url": os.environ.get("AI4PROPOSAL_IMAGE_BASE_URL", "https://api.chatanywhere.tech/v1"),
        "model": os.environ.get("AI4PROPOSAL_IMAGE_MODEL", "gpt-image-2"),
    }
