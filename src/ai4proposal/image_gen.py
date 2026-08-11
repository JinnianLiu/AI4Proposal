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
import re
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

# Some corporate/proxy chains break cert validation; mirror evidence.py's context.
_SSL_VERIFIED = ssl.create_default_context()

_SSL_UNVERIFIED = ssl.create_default_context()
_SSL_UNVERIFIED.check_hostname = False
_SSL_UNVERIFIED.verify_mode = ssl.CERT_NONE

_warned_insecure = False


def _urlopen(req: urllib.request.Request, timeout: float):
    """Open `req` with certificate verification, dropping to an unverified
    connection only when the TLS chain itself fails (proxies that terminate TLS
    with their own CA). Mirrors evidence._urlopen. This path carries the image
    API key, so verification matters more here than for public retrieval.
    Set AI4PROPOSAL_INSECURE_TLS=1 to skip the verified attempt."""
    global _warned_insecure
    insecure = os.getenv("AI4PROPOSAL_INSECURE_TLS", "").strip().lower() in {"1", "true", "yes", "on"}
    if not insecure:
        try:
            return urllib.request.urlopen(req, timeout=timeout, context=_SSL_VERIFIED)
        except ssl.SSLError:
            if not _warned_insecure:
                print("    [image] TLS 证书校验失败（可能是代理拆包），改用不校验连接")
                _warned_insecure = True
    return urllib.request.urlopen(req, timeout=timeout, context=_SSL_UNVERIFIED)

# ── Renderer template (adapted from the Architect / Renderer two-stage workflow) ──
# Strict art style + text constraints + exact layout execution, wrapped around the
# figure description. Language-aware: labels follow the proposal language instead
# of forcing English onto Chinese proposals.

# Default academic colour palette (HEX): soft light fills, strong accents, and a
# slate grey for text / lines / arrows — the palette of high-quality paper figures.
# Overridable via AI4PROPOSAL_FIGURE_PALETTE (comma-separated HEX codes).
# Landscape by default: a square canvas forces multi-panel figures to stack
# vertically and cramps them. Override with AI4PROPOSAL_IMAGE_SIZE.
DEFAULT_SIZE = "1536x1024"

DEFAULT_PALETTE = [
    "#E3F2FD",  # light blue fill
    "#1E88E5",  # blue accent
    "#E8F5E9",  # light green fill
    "#43A047",  # green accent
    "#FFF3E0",  # light orange fill
    "#37474F",  # slate grey: text / lines / arrows
]

RENDERER_TEMPLATE = """Render a technical figure for a research grant proposal at top-tier academic paper quality (CVPR/NeurIPS style).

ART STYLE (strict):
- Flat 2D vector illustration, clean geometric shapes, thin crisp outlines, soft desaturated fills, plain white background.
- NOT photorealistic, NOT 3D-rendered, NOT hand-drawn; no gradients, shadows, gloss, or decorative texture.

COLOR PALETTE (strict):
- Use ONLY these colors: {palette} (HEX codes). Never introduce extra hues.

TEXT CONSTRAINTS (critical):
- All visible text must be in {language}.
- Render ONLY the short labels and annotations explicitly described below (module names, 标注内容, arrow labels). Keep every label short.
- NEVER render structural or meta words such as "布局", "分区", "Zone", "Zone 1", "Container", "Layout", "Label", "Input", "Output".
- Clean sans-serif font: Chinese text in 黑体 / Noto Sans CJK, Latin text in Helvetica / Roboto.
- No watermark, no logo, no credit line.

LAYOUT EXECUTION (strict):
- Reproduce the described layout, zones, module positions and arrow directions EXACTLY as specified. Do not reorder, merge, simplify, or "improve" the flow.
- Solid arrow = data/flow; dashed arrow = feedback/control. Follow the stated start and end points.

DATA HONESTY:
- Conceptual schematic only: axes and charts may show placeholder ticks and schematic bars, but NO specific numeric values, data points, or statistics. Do not invent numbers.

=== FIGURE DESCRIPTION ===
{schema}"""


def _is_hex_color(value: str) -> bool:
    return re.fullmatch(r"#[0-9A-Fa-f]{6}", value) is not None


def figure_palette_from_env() -> list:
    """Read AI4PROPOSAL_FIGURE_PALETTE (comma-separated HEX), fall back to the default palette."""
    raw = os.environ.get("AI4PROPOSAL_FIGURE_PALETTE", "")
    if raw:
        colors = [c.strip() for c in raw.split(",") if c.strip()]
        if colors and all(_is_hex_color(c) for c in colors):
            return colors[:6]
    return list(DEFAULT_PALETTE)


def build_image_prompt(
    desc: str,
    style_prefix: bool = True,
    language: str = "zh",
    palette: Optional[list] = None,
) -> str:
    """The exact string sent to the image model = renderer template + the figure description.

    `style_prefix=False` returns the description unchanged (used when a saved
    full prompt already has the renderer baked in).
    """
    if not style_prefix:
        return desc
    pal = palette if palette else figure_palette_from_env()
    lang = "简体中文" if language in ("zh", "cn", "zh-CN", "zh_CN") else "English"
    return RENDERER_TEMPLATE.format(palette=", ".join(pal), language=lang, schema=desc)


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
    size: str = DEFAULT_SIZE,
    timeout: float = 180.0,
    max_retries: int = 2,
    style_prefix: bool = True,
    language: str = "zh",
    palette: Optional[list] = None,
) -> Optional[Path]:
    """Generate one image and save it as PNG. Returns the path, or None on failure."""
    if not api_key:
        return None
    full_prompt = build_image_prompt(prompt, style_prefix, language=language, palette=palette)
    url = _endpoint(base_url)
    body = json.dumps({"model": model, "prompt": full_prompt, "n": 1, "size": size}).encode()
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    data = None
    for attempt in range(max_retries + 1):
        try:
            req = urllib.request.Request(url, data=body, headers=headers)
            with _urlopen(req, timeout) as resp:
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
            with _urlopen(req, timeout) as resp:
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
        "size": os.environ.get("AI4PROPOSAL_IMAGE_SIZE", DEFAULT_SIZE).strip() or DEFAULT_SIZE,
    }
