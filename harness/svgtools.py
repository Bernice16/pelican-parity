"""SVG Extractor & Renderer.

Pulls one SVG out of the model's text answer, checks it is well-formed and
self-contained, and renders a PNG with a pinned renderer at a fixed width.
"""
import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path


class SvgError(RuntimeError):
    """The model's answer holds no usable SVG: a new session is needed."""


class RenderError(RuntimeError):
    """The SVG is fine but rendering failed (local problem): no new session needed."""


_FENCED = re.compile(r"```(?:svg|xml)?\s*(<svg\b.*?</svg>)\s*```", re.S | re.I)
_BARE = re.compile(r"(<svg\b.*?</svg>)", re.S | re.I)
_HREF_ATTRS = {"href", "{http://www.w3.org/1999/xlink}href"}


def extract_svg(text: str) -> str:
    match = _FENCED.search(text) or _BARE.search(text)
    if not match:
        raise SvgError("no <svg>...</svg> found in the model answer")
    return match.group(1).strip() + "\n"


def validate_svg(svg: str) -> None:
    try:
        root = ET.fromstring(svg)
    except ET.ParseError as e:
        raise SvgError(f"SVG is not well-formed XML: {e}") from e
    if not root.tag.endswith("svg"):
        raise SvgError(f"root element is <{root.tag}>, not <svg>")
    for el in root.iter():
        if el.tag.endswith("script"):
            raise SvgError("SVG contains a <script> element")
        for attr, value in el.attrib.items():
            if attr in _HREF_ATTRS and not value.startswith(("#", "data:")):
                raise SvgError(f"SVG references an external resource: {value[:80]}")


def renderer_version(cfg: dict) -> str:
    try:
        out = subprocess.run(cfg["render"]["version_cmd"], capture_output=True, text=True)
    except FileNotFoundError as e:
        raise RenderError(f"renderer not found: {cfg['render']['version_cmd'][0]}") from e
    return (out.stdout or out.stderr).strip()


FONTS_DIR = Path(__file__).resolve().parent.parent / "fonts"


def render_png(cfg: dict, svg_path: Path, png_path: Path) -> None:
    values = {"width": str(cfg["render"]["width"]), "svg": str(svg_path), "png": str(png_path),
              "fonts_dir": str(FONTS_DIR)}
    cmd = [part.format(**values) for part in cfg["render"]["cmd"]]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError as e:
        raise RenderError(f"renderer not found: {cmd[0]}") from e
    if proc.returncode != 0 or not png_path.exists():
        raise RenderError(f"render failed: {proc.stderr.strip()[:300]}")
