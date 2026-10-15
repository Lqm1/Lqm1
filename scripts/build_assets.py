"""Generate the SVG assets used by README.md.

All text is converted to outlines so the images look the same on every
platform (GitHub renders SVGs as <img>, so web fonts can't be loaded).

Requirements:
    pip install fonttools brotli numpy pillow

Usage:
    python scripts/build_assets.py \
        --inter /usr/share/fonts/opentype/inter \
        --noto-jp path/to/@fontsource-variable/noto-sans-jp/files \
        --images path/to/lami.zip/public
"""

from __future__ import annotations

import argparse
import base64
import io
import math
from xml.sax.saxutils import escape
from functools import lru_cache
from pathlib import Path

import numpy as np
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets"

# --------------------------------------------------------------------------
# Themes (colors follow the lami.zip design tokens)
# --------------------------------------------------------------------------

THEMES = {
    "light": {
        "page": "#f8f7fb",
        "card": "#ffffff",
        "line": "#ececf0",
        "line_strong": "#d9d9de",
        "ink": "#111111",
        "text": "#42424a",
        "muted": "#666666",
        "subtle": "#888888",
        "on_ink": "#ffffff",
        "tones": {
            "pink": ("#ffdff4", "#ffd2ee", "#9b2f78"),
            "sky": ("#e3f1ff", "#cde5ff", "#245f9e"),
            "mint": ("#dcfbf3", "#c5f4e7", "#1d7a62"),
            "lavender": ("#eee7ff", "#dfd2ff", "#5b3fb0"),
        },
        "glow": ("rgba(255,214,244,0.75)", "rgba(207,231,255,0.85)", "rgba(194,249,229,0.6)"),
        "accent": ("#ffd2ee", "#cde5ff", "#c5f4e7"),
    },
    "dark": {
        "page": "#0f0f14",
        "card": "#16161d",
        "line": "#25252e",
        "line_strong": "#33333d",
        "ink": "#f4f4f7",
        "text": "#c2c2cc",
        "muted": "#9a9aa5",
        "subtle": "#7c7c87",
        "on_ink": "#111111",
        "tones": {
            "pink": ("#33192c", "#4a2441", "#ffb8e6"),
            "sky": ("#15233a", "#203452", "#a9d2ff"),
            "mint": ("#11291f", "#1b3d30", "#9ef0d6"),
            "lavender": ("#231c3b", "#33295a", "#cbb8ff"),
        },
        "glow": ("rgba(190,70,160,0.30)", "rgba(70,120,220,0.32)", "rgba(40,170,130,0.22)"),
        "accent": ("#6b2f5c", "#2c4c7a", "#245c4a"),
    },
}

# --------------------------------------------------------------------------
# Fonts → outlines
# --------------------------------------------------------------------------


class Fonts:
    def __init__(self, inter_dir: Path, noto_dir: Path):
        self.inter_files = {
            400: inter_dir / "Inter-Regular.otf",
            500: inter_dir / "Inter-Medium.otf",
            600: inter_dir / "Inter-SemiBold.otf",
            700: inter_dir / "Inter-Bold.otf",
            800: inter_dir / "Inter-ExtraBold.otf",
            900: inter_dir / "Inter-Black.otf",
        }
        self.noto_files = sorted(noto_dir.glob("noto-sans-jp-*-wght-normal.woff2"))
        self._noto_index: dict[int, Path] | None = None

    @lru_cache(maxsize=None)
    def inter(self, weight: int) -> TTFont:
        return TTFont(self.inter_files[weight])

    def noto_index(self) -> dict[int, Path]:
        if self._noto_index is None:
            index: dict[int, Path] = {}
            for f in self.noto_files:
                for cp in TTFont(f).getBestCmap():
                    index.setdefault(cp, f)
            self._noto_index = index
        return self._noto_index

    @lru_cache(maxsize=None)
    def noto(self, path: Path, weight: int) -> TTFont:
        font = TTFont(path)
        return instantiateVariableFont(font, {"wght": weight})

    def font_for(self, ch: str, weight: int) -> TTFont:
        cp = ord(ch)
        inter = self.inter(weight)
        if cp < 0x3000 and cp in inter.getBestCmap():
            return inter
        path = self.noto_index().get(cp)
        if path is None:
            return inter
        return self.noto(path, weight)


FONTS: Fonts


def _num(v: float) -> str:
    s = f"{v:.1f}"
    return s[:-2] if s.endswith(".0") else s


def measure(text: str, size: float, weight: int, tracking: float = 0) -> float:
    w = 0.0
    for ch in text:
        font = FONTS.font_for(ch, weight)
        upm = font["head"].unitsPerEm
        gname = font.getBestCmap().get(ord(ch))
        adv = font["hmtx"][gname][0] if gname else upm * 0.5
        w += adv * size / upm + tracking
    return w - (tracking if text else 0)


def text_path(
    text: str,
    x: float,
    y: float,
    size: float,
    weight: int,
    fill: str,
    anchor: str = "start",
    tracking: float = 0,
    extra: str = "",
) -> str:
    if anchor != "start":
        w = measure(text, size, weight, tracking)
        x -= w if anchor == "end" else w / 2
    parts = []
    cx = x
    for ch in text:
        font = FONTS.font_for(ch, weight)
        upm = font["head"].unitsPerEm
        gname = font.getBestCmap().get(ord(ch))
        if gname is None:
            cx += size * 0.5 + tracking
            continue
        scale = size / upm
        pen = SVGPathPen(font.getGlyphSet(), ntos=_num)
        font.getGlyphSet()[gname].draw(TransformPen(pen, (scale, 0, 0, -scale, cx, y)))
        d = pen.getCommands()
        if d:
            parts.append(d)
        cx += font["hmtx"][gname][0] * scale + tracking
    return f'<path fill="{fill}" {extra} d="{"".join(parts)}"/>'


def wrap(text: str, size: float, weight: int, max_width: float) -> list[str]:
    words = text.split(" ")
    lines: list[str] = []
    cur = ""
    for word in words:
        trial = f"{cur} {word}".strip()
        if measure(trial, size, weight) <= max_width:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


# --------------------------------------------------------------------------
# Images
# --------------------------------------------------------------------------


def _darken(img: Image.Image, base: str) -> Image.Image:
    """Invert lightness but keep hue, so pastel-on-white art reads on dark."""
    a = np.asarray(img.convert("RGB")).astype(np.float32) / 255
    inv = 1 - a
    # rotate hue 180° around the gray axis (undo the hue flip of the inversion)
    mean = inv.mean(axis=2, keepdims=True)
    out = mean - (inv - mean)
    b = np.array([int(base[i : i + 2], 16) for i in (1, 3, 5)], dtype=np.float32) / 255
    out = np.clip(b + out * (0.95 - b), 0, 1)
    return Image.fromarray((out * 255).astype(np.uint8))


def data_uri(path: Path, size: tuple[int, int], base: str | None, inset: float = 0) -> str:
    """Load an image, optionally trim `inset` (fraction) from each side, and
    remap it onto the dark `base` color when given."""
    img = Image.open(path).convert("RGB")
    if inset:
        w, h = img.size
        img = img.crop((round(w * inset), round(h * inset), round(w * (1 - inset)), round(h * (1 - inset))))
    img = img.resize(size, Image.LANCZOS)
    if base:
        img = _darken(img, base)
    buf = io.BytesIO()
    img.save(buf, "WEBP", quality=82, method=6)
    return "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode()


# --------------------------------------------------------------------------
# Building blocks
# --------------------------------------------------------------------------


def svg(w: int, h: int, body: str, title: str, style: str = "") -> str:
    style_tag = f"<style>{style}</style>" if style else ""
    title = escape(title, {'"': "&quot;"})
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}" role="img" aria-label="{title}">'
        f"<title>{title}</title>{style_tag}{body}</svg>\n"
    )


def chip(x: float, y: float, label: str, tone: str, t: dict, size: float = 15) -> tuple[str, float]:
    bg, line, fg = t["tones"][tone]
    pad = size * 0.9
    w = measure(label, size, 600) + pad * 2
    h = size * 2.1
    body = (
        f'<rect x="{x}" y="{y}" width="{_num(w)}" height="{_num(h)}" rx="{_num(h / 2)}" '
        f'fill="{bg}" stroke="{line}"/>'
        + text_path(label, x + pad, y + h / 2 + size * 0.36, size, 600, fg)
    )
    return body, w


def arrow(cx: float, cy: float, r: float, t: dict) -> str:
    s = r * 0.42
    return (
        f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{t["card"]}" stroke="{t["line_strong"]}"/>'
        f'<path d="M{_num(cx - s)} {_num(cy + s)} L{_num(cx + s)} {_num(cy - s)} M{_num(cx - s * 0.35)} {_num(cy - s)} '
        f'L{_num(cx + s)} {_num(cy - s)} L{_num(cx + s)} {_num(cy + s * 0.35)}" fill="none" '
        f'stroke="{t["ink"]}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>'
    )


def glow_defs(prefix: str, t: dict, spots: list[tuple[float, float, float, int]]) -> tuple[str, str]:
    """Soft radial blobs like the lami.zip hero background."""
    defs, body = [], []
    for i, (cx, cy, r, ci) in enumerate(spots):
        gid = f"{prefix}g{i}"
        defs.append(
            f'<radialGradient id="{gid}"><stop offset="0" stop-color="{t["glow"][ci]}"/>'
            f'<stop offset="1" stop-color="{t["glow"][ci]}" stop-opacity="0"/></radialGradient>'
        )
        body.append(f'<circle class="{prefix}b{i}" cx="{cx}" cy="{cy}" r="{r}" fill="url(#{gid})"/>')
    return "".join(defs), "".join(body)


# --------------------------------------------------------------------------
# Assets
# --------------------------------------------------------------------------

HERO_PHRASES = [
    ("Turning ideas into", "web that feels good."),
    ("Easy to use,", "carefully engineered."),
    ("Ship small and fast,", "then grow it with care."),
]


def header(t: dict, dark: bool, images: Path) -> str:
    W, H = 1200, 520
    hero = data_uri(images / "hero-iridescent.webp", (1030, 550), t["page"] if dark else None)
    gdefs, gbody = glow_defs("h", t, [(880, 230, 330, 0), (700, 90, 300, 1), (1040, 450, 260, 2)])

    phrases = []
    for i, (a, b) in enumerate(HERO_PHRASES):
        phrases.append(
            f'<g class="p p{i}">'
            + text_path(a, 64, 238, 58, 800, t["ink"])
            + text_path(b, 64, 314, 58, 800, t["ink"])
            + "</g>"
        )

    chips, cx = [], 64.0
    for label, tone in [("Based in Japan", "pink"), ("TypeScript · Python · Rust", "sky"), ("Cloudflare Workers", "mint")]:
        c, w = chip(cx, 420, label, tone, t, 16)
        chips.append(c)
        cx += w + 10

    lead = wrap(
        "Full-stack developer building web apps, APIs, Discord bots and Japanese voice tools — from design to deploy.",
        21, 500, 600,
    )
    lead_svg = "".join(text_path(line, 64, 362 + i * 31, 21, 500, t["text"]) for i, line in enumerate(lead))

    style = """
    .hero{animation:float 14s ease-in-out infinite;transform-box:fill-box;transform-origin:center}
    @keyframes float{0%,100%{transform:translate(0,0) scale(1)}50%{transform:translate(-12px,8px) scale(1.035)}}
    .hb0{animation:drift 11s ease-in-out infinite}
    .hb1{animation:drift 13s ease-in-out infinite reverse}
    @keyframes drift{0%,100%{transform:translate(0,0)}50%{transform:translate(18px,-10px)}}
    .p{opacity:0;animation:cycle 22.8s infinite}
    .p1{animation-delay:7.6s}.p2{animation-delay:15.2s}
    @keyframes cycle{0%{opacity:0;transform:translateY(12px)}3%,30%{opacity:1;transform:translateY(0)}
      33.3%,100%{opacity:0;transform:translateY(-10px)}}
    @media (prefers-reduced-motion:reduce){.hero,.hb0,.hb1{animation:none}.p{animation:none}.p0{opacity:1}}
    """
    body = f"""
<defs>
  <clipPath id="hc"><rect width="{W}" height="{H}" rx="40"/></clipPath>
  <radialGradient id="hm" cx="0.6" cy="0.5" r="0.5"><stop offset="0.3" stop-color="#fff"/><stop offset="1" stop-color="#000"/></radialGradient>
  <mask id="hmask"><rect x="300" y="-20" width="1030" height="550" fill="url(#hm)"/></mask>
  {gdefs}
</defs>
<g clip-path="url(#hc)">
  <rect width="{W}" height="{H}" fill="{t['page']}"/>
  {gbody}
  <g mask="url(#hmask)" opacity="{0.9 if dark else 0.95}"><image class="hero" href="{hero}" x="300" y="-20" width="1030" height="550"/></g>
</g>
<rect x="0.5" y="0.5" width="{W - 1}" height="{H - 1}" rx="40" fill="none" stroke="{t['line']}"/>
{text_path("LAMI  ·  WORKS", 64, 96, 15, 700, t["subtle"], tracking=3.6)}
{text_path("Lami", 64, 150, 34, 800, t["ink"])}
{"".join(phrases)}
{lead_svg}
{"".join(chips)}
"""
    return svg(W, H, body, "Lami — full-stack developer building web apps, APIs, Discord bots and voice tools", style)


def button(label: str, primary: bool, t: dict, icon: str = "") -> str:
    size, h, pad = 17, 52, 26
    w = measure(label, size, 600) + pad * 2 + (26 if icon else 0)
    W = math.ceil(w) + 2
    fill = t["ink"] if primary else t["card"]
    fg = t["on_ink"] if primary else t["ink"]
    stroke = t["ink"] if primary else t["line_strong"]
    icon_svg = ""
    tx = pad + 1
    if icon:
        icon_svg = f'<g transform="translate({pad + 1} {h / 2 - 9})" fill="none" stroke="{fg}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">{icon}</g>'
        tx += 26
    body = (
        f'<rect x="1" y="1" width="{W - 2}" height="{h - 2}" rx="{h / 2 - 1}" fill="{fill}" stroke="{stroke}"/>'
        + icon_svg
        + text_path(label, tx, h / 2 + size * 0.36, size, 600, fg)
    )
    return svg(W, h, body, label)


ICONS = {
    "globe": '<circle cx="9" cy="9" r="8"/><path d="M1 9h16M9 1c2.5 2.5 3.5 5.2 3.5 8S11.5 14.5 9 17C6.5 14.5 5.5 11.8 5.5 9S6.5 3.5 9 1z"/>',
    "pen": '<path d="M12.5 2.5l3 3L6 15H3v-3z"/><path d="M10.5 4.5l3 3"/>',
    "mail": '<rect x="1" y="3" width="16" height="12" rx="2.5"/><path d="M1.5 4.5L9 10l7.5-5.5"/>',
    "camera": '<rect x="1" y="1" width="16" height="16" rx="4.5"/><circle cx="9" cy="9" r="3.6"/><circle cx="13.4" cy="4.6" r="0.4"/>',
}


def section_title(label: str, heading: str, t: dict, tone_i: int) -> str:
    W, H = 1200, 132
    hw = measure(heading, 40, 800)
    body = (
        text_path(label, 8, 40, 14, 700, t["subtle"], tracking=3.4)
        + f'<rect x="4" y="96" width="{_num(hw + 10)}" height="16" rx="8" fill="{t["accent"][tone_i]}"/>'
        + text_path(heading, 8, 104, 40, 800, t["ink"])
    )
    return svg(W, H, body, f"{label} — {heading}")


PROJECTS = [
    {
        "slug": "yomi-chan",
        "title": "Yomi-chan",
        "label": "Discord Bot",
        "tone": "pink",
        "desc": "Japanese text-to-speech bot for Discord, with VOICEVOX, COEIROINK, AivisSpeech and AquesTalk voices.",
        "meta": "yomi-chan.jp",
        "image": "project-discord-voice.webp",
    },
    {
        "slug": "avennota",
        "title": "Avennota",
        "label": "Web / Desktop App",
        "tone": "sky",
        "desc": "A modern oto.ini editor for UTAU voicebanks that runs in the browser and on the desktop.",
        "meta": "avennota.lami.zip",
        "image": "project-voicebank.webp",
    },
    {
        "slug": "artifacter",
        "title": "Artifacter",
        "label": "Web App",
        "tone": "lavender",
        "desc": "Turn a Genshin Impact UID into a build card with artifact scores. Five languages, rendered on the edge.",
        "meta": "artifacter.lami.zip",
        "image": "project-transaction.webp",
    },
    {
        "slug": "traveler-assistant",
        "title": "Traveler Assistant",
        "label": "Browser Extension",
        "tone": "mint",
        "desc": "Daily notes and build cards from HoYoLAB, right in your browser. For Genshin Impact players.",
        "meta": "Chrome Web Store",
        "image": "blog-visual.webp",
    },
    {
        "slug": "x-client-transaction-id",
        "title": "x-client-transaction-id",
        "label": "Library",
        "tone": "sky",
        "desc": "Generates the x-client-transaction-id header for X (Twitter) API requests. Published on npm and JSR.",
        "meta": "TypeScript · npm · JSR",
        "image": "project-portfolio.webp",
    },
    {
        "slug": "pi-codex-app-server",
        "title": "pi-codex-app-server",
        "label": "Agent Tooling",
        "tone": "pink",
        "desc": "Talk to Pi Coding Agent from anything that speaks the Codex app-server protocol — even the ChatGPT app.",
        "meta": "TypeScript · Pi package",
        "image": "project-workers-ai.webp",
    },
]


def project_card(p: dict, t: dict, dark: bool, images: Path) -> str:
    W, H = 600, 320
    bg, line, _ = t["tones"][p["tone"]]
    img = data_uri(images / p["image"], (360, 360), t["card"] if dark else None, inset=0.07)
    title_size = 30 if measure(p["title"], 30, 800) <= 330 else 25
    desc = wrap(p["desc"], 17, 500, 318)[:4]
    chip_svg, _ = chip(30, 30, p["label"], p["tone"], t, 14)
    body = f"""
<defs>
  <clipPath id="cc"><rect width="{W}" height="{H}" rx="28"/></clipPath>
  <clipPath id="ci"><rect x="384" y="104" width="190" height="190" rx="26"/></clipPath>
  <radialGradient id="cg" cx="0.15" cy="0.05" r="0.9"><stop offset="0" stop-color="{bg}"/><stop offset="0.55" stop-color="{bg}" stop-opacity="0"/></radialGradient>
</defs>
<g clip-path="url(#cc)">
  <rect width="{W}" height="{H}" fill="{t['card']}"/>
  <rect width="{W}" height="{H}" fill="url(#cg)"/>
</g>
<rect x="0.5" y="0.5" width="{W - 1}" height="{H - 1}" rx="28" fill="none" stroke="{t['line']}"/>
<g clip-path="url(#ci)"><image href="{img}" x="384" y="104" width="190" height="190"/></g>
<rect x="384.5" y="104.5" width="189" height="189" rx="26" fill="none" stroke="{line}"/>
{chip_svg}
{arrow(552, 50, 21, t)}
{text_path(p["title"], 30, 150, title_size, 800, t["ink"])}
{"".join(text_path(l, 30, 190 + i * 26, 17, 500, t["text"]) for i, l in enumerate(desc))}
{text_path(p["meta"], 30, 294, 14, 600, t["subtle"], tracking=0.4)}
"""
    return svg(W, H, body, f'{p["title"]} — {p["desc"]}')


STACK = [
    ("Languages", "TypeScript, Python, Rust, Go"),
    ("Frontend", "React, TanStack Start, Next.js, Tailwind CSS"),
    ("Backend", "Hono, Elysia, FastAPI, Express"),
    ("Runtime / Infra", "Cloudflare Workers, Bun, Node.js, Deno, Docker"),
    ("Database", "PostgreSQL, Prisma, MySQL, MongoDB"),
    ("Voice", "VOICEVOX, AquesTalk, OpenJTalk, UTAU, DiffSinger"),
]


def stack_panel(t: dict) -> str:
    W = 1200
    row_h = 62
    H = 48 + row_h * len(STACK)
    gdefs, gbody = glow_defs("s", t, [(1080, 40, 380, 2), (120, H, 300, 1)])
    rows = []
    tones = ["pink", "sky", "mint", "lavender", "sky", "pink"]
    for i, (cat, items) in enumerate(STACK):
        y = 24 + i * row_h
        cy = y + row_h / 2
        dot = t["tones"][tones[i]][2 if t is THEMES["dark"] else 1]
        rows.append(
            f'<circle cx="52" cy="{cy}" r="7" fill="{dot}"/>'
            + text_path(cat, 74, cy + 6, 17, 700, t["ink"])
            + text_path(items, 290, cy + 6, 17, 500, t["text"])
            + (f'<rect x="40" y="{y + row_h}" width="{W - 80}" height="1" fill="{t["line"]}"/>' if i < len(STACK) - 1 else "")
        )
    body = f"""
<defs><clipPath id="sc"><rect width="{W}" height="{H}" rx="28"/></clipPath>{gdefs}</defs>
<g clip-path="url(#sc)"><rect width="{W}" height="{H}" fill="{t['card']}"/>{gbody}</g>
<rect x="0.5" y="0.5" width="{W - 1}" height="{H - 1}" rx="28" fill="none" stroke="{t['line']}"/>
{"".join(rows)}
"""
    return svg(W, H, body, "Tech stack: " + "; ".join(f"{c}: {i}" for c, i in STACK))


GAMES = [
    ("genshin", "Genshin Impact", "895578273", "lavender"),
    ("hsr", "Honkai: Star Rail", "834466088", "sky"),
    ("zzz", "Zenless Zone Zero", "1311324570", "pink"),
]


def game_pill(name: str, uid: str, tone: str, t: dict) -> str:
    W, H = 390, 92
    bg, line, fg = t["tones"][tone]
    body = (
        f'<rect x="0.5" y="0.5" width="{W - 1}" height="{H - 1}" rx="24" fill="{t["card"]}" stroke="{t["line"]}"/>'
        f'<rect x="18" y="18" width="56" height="56" rx="16" fill="{bg}" stroke="{line}"/>'
        f'<path d="M46 30 L50.5 41.5 L62 46 L50.5 50.5 L46 62 L41.5 50.5 L30 46 L41.5 41.5 Z" fill="{fg}" opacity="0.85"/>'
        + text_path(name, 92, 42, 18, 700, t["ink"])
        + text_path("UID " + uid, 92, 68, 15, 500, t["subtle"], tracking=0.6)
        + arrow(W - 40, H / 2, 17, t)
    )
    return svg(W, H, body, f"{name} — UID {uid} on Enka.Network")


def main() -> None:
    global FONTS
    ap = argparse.ArgumentParser()
    ap.add_argument("--inter", type=Path, default=Path("/usr/share/fonts/opentype/inter"))
    ap.add_argument("--noto-jp", type=Path, required=True)
    ap.add_argument("--images", type=Path, required=True)
    args = ap.parse_args()
    FONTS = Fonts(args.inter, args.noto_jp)
    OUT.mkdir(exist_ok=True)

    for mode, t in THEMES.items():
        dark = mode == "dark"
        files = {
            f"header-{mode}.svg": header(t, dark, args.images),
            f"stack-{mode}.svg": stack_panel(t),
            f"btn-portfolio-{mode}.svg": button("Portfolio", True, t, ICONS["globe"]),
            f"btn-blog-{mode}.svg": button("Blog", False, t, ICONS["pen"]),
            f"btn-mail-{mode}.svg": button("info@lami.zip", False, t, ICONS["mail"]),
            f"btn-instagram-{mode}.svg": button("Instagram", False, t, ICONS["camera"]),
            f"title-projects-{mode}.svg": section_title("FEATURED WORK", "Projects", t, 0),
            f"title-libraries-{mode}.svg": section_title("OPEN SOURCE", "Libraries & Tools", t, 1),
            f"title-stack-{mode}.svg": section_title("TOOLBOX", "Tech Stack", t, 2),
            f"title-hoyoverse-{mode}.svg": section_title("OFF THE CLOCK", "HoYoverse", t, 0),
        }
        for p in PROJECTS:
            files[f"card-{p['slug']}-{mode}.svg"] = project_card(p, t, dark, args.images)
        for slug, name, uid, tone in GAMES:
            files[f"game-{slug}-{mode}.svg"] = game_pill(name, uid, tone, t)
        for name, content in files.items():
            (OUT / name).write_text(content, encoding="utf-8")
            print(f"{name}: {len(content.encode()) // 1024} KB")


if __name__ == "__main__":
    main()
