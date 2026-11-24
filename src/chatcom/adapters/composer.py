"""The composer: writes one HyperFrames project for an episode from its
timeline (core/render.py). It places what the timeline says; it decides no time.

    Timeline + cast.next.json + the show folder
        -> episodes/<code>/render/index.html   one standalone composition, 60 s
           episodes/<code>/render/assets/      linked (hard link, else copy) pictures and fonts

Render safety (PLAN.md, docs/design/render.md S2/O7):
- chat- and model-derived text enters only as escaped text nodes;
- no URL that leaves the build folder: every src is `assets/...`, and a CSP meta
  tag allows only the page's own origin;
- a picture whose path leaves the show folder is refused, so nothing outside
  the show reaches the build.

Layout rules (O9): one bubble on screen at a time (the timeline never overlaps
them), above the drawn figures, as wide as its text (at most 940 px, 52 px type),
on the speaker's side of the screen, like a chat, with its tail above the
speaker (a short bubble widens just enough to reach); a sticker sits on the
bubble's top edge. The figures of
a scene stand side by side along the bottom, in order of their first line, each
in its current pose: their bubble's pose while they speak, neutral otherwise.
A chorus speaker (no drawing) gets a bubble with their name and no tail."""
from __future__ import annotations

import html
import os
import shutil
from importlib import resources
from pathlib import Path

from chatcom.core import render as rt
from chatcom.core.errors import ChatcomError

W, H = 1080, 1920
CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data: blob:; font-src 'self' data:; media-src 'self' data: blob:; connect-src 'self'")
PALETTE = ("#D9480F", "#0B7285", "#862E9C", "#2B8A3E", "#C2255C", "#1864AB", "#E67700", "#5F3DC4")
STICKERS = {"laugh": ("😂", "laughter"), "applause": ("👏", "applause"), "gasp": ("😮", "gasp"),
            "aww": ("🥹", "aww"), "groan": ("😩", "groan")}
SMALL_FONTS = (  # (family, file, weight, style)
    ("Space Grotesk", "SpaceGrotesk-Medium.woff2", 500, "normal"),
    ("Space Grotesk", "SpaceGrotesk-Bold.woff2", 700, "normal"),
    ("JetBrains Mono", "JetBrainsMono-Medium.woff2", 500, "normal"),
    ("JetBrains Mono", "JetBrainsMono-Bold.woff2", 700, "normal"),
    ("DM Serif Display", "DMSerifDisplay-Italic.woff2", 400, "italic"),
    ("Bricolage Grotesque", "Bricolage-ExtraBold.woff2", 800, "normal"),
)
BIG_FONTS = (  # (family, file): from the runtime (adapters/runtime.py)
    ("Noto Color Emoji", "NotoColorEmoji-Regular.ttf"),
    ("Noto Sans SC", "NotoSansSC[wght].ttf"),
    ("Noto Sans KR", "NotoSansKR[wght].ttf"),
)
BUB_EDGE = 70     # px from the screen edge to a bubble (episode.css .bub.l / .bub.r)
TAIL_HALF = 24    # half the tail's width
TAIL_MIN = 40     # the tail never sits closer than this to the bubble's corner
FIG_MAX_W = 560
FIG_MAX_H = 980


def e(text) -> str:
    return html.escape(str(text), quote=True)


def _t(x: float) -> str:
    return f"{x:.3f}".rstrip("0").rstrip(".")


def _win(*pairs: tuple[float, float]) -> str:
    return ";".join(f"{_t(a)},{_t(b)}" for a, b in pairs)


class Assets:
    """Copies of the pictures and fonts the page uses, inside the build folder.
    `add` refuses a source outside its allowed root."""

    def __init__(self, build: Path):
        self.build = build
        self.done: dict[str, str] = {}

    def add(self, src: Path, rel: str, *, inside: Path | None, what: str, fix: str) -> str:
        real = src.resolve()
        if inside is not None and not real.is_relative_to(inside.resolve()):
            raise ChatcomError("CHATCOM_E_STATE", f"{what} points outside the show folder: {src}", fix,
                               context={"path": str(src)})
        if not real.is_file():
            raise ChatcomError("CHATCOM_E_STATE", f"{what} is missing: {src}", fix, context={"path": str(src)})
        if rel not in self.done:
            dest = self.build / "assets" / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.unlink(missing_ok=True)
            try:
                os.link(real, dest)
            except OSError:
                shutil.copyfile(real, dest)
            self.done[rel] = str(real)
        return "assets/" + rel


def _members(sheet: dict) -> dict[str, tuple[int, dict]]:
    return {m["name"]: (i, m) for i, m in enumerate(sheet["members"])}


def _pose_src(a: Assets, show: Path, members, name: str, pose: str) -> str:
    i, m = members[name]
    return a.add(show / m["poses"][pose]["file"], f"cast/m{i}/{pose}.png", inside=show,
                 what=f"{name}'s '{pose}' drawing", fix="Run `chatcom recast <show> --member <name>` to draw it "
                 "again inside the show folder, then render again.")


def _bg_src(a: Assets, show: Path, sheet: dict, bg_id: str) -> str:
    ids = [b["id"] for b in sheet["backgrounds"]]
    b = sheet["backgrounds"][ids.index(bg_id)]
    return a.add(show / b["file"], f"bg/b{ids.index(bg_id)}.png", inside=show, what=f"background '{bg_id}'",
                 fix="Run `chatcom recast <show>` to draw the sets again inside the show folder, then render again.")


def _figures(seg: rt.Segment, members) -> list[dict]:
    """Where each drawn speaker stands, and which pose shows when."""
    n = len(seg.cast)
    if not n:
        return []
    w = min(FIG_MAX_W, int(W / n * 1.25))
    out = []
    for k, name in enumerate(seg.cast):
        cx = int(W * (k + 0.5) / n)
        talk = [(b.start, b.end, b.pose) for b in seg.bubbles if b.speaker == name and b.pose]
        poses = set(members[name][1]["poses"])
        rest = "neutral" if "neutral" in poses else talk[0][2]
        shows: dict[str, list[tuple[float, float]]] = {}
        t = seg.start
        for a, b, pose in talk:
            if a > t:
                shows.setdefault(rest, []).append((t, a))
            shows.setdefault(pose, []).append((a, b))
            t = b
        if t < seg.start + seg.length:
            shows.setdefault(rest, []).append((t, seg.start + seg.length))
        out.append({"name": name, "cx": cx, "left": cx - w // 2, "width": w, "height": min(FIG_MAX_H, w * 4 // 3),
                    "shows": shows,
                    "talk": [(a, b) for a, b, _ in talk]})
    return out


def _scene(a: Assets, show: Path, sheet, members, seg: rt.Segment, colour) -> str:
    end = seg.start + seg.length
    figs = _figures(seg, members)
    where = {f["name"]: f["cx"] for f in figs}
    parts = [f'<div class="bg" style="background-image:url(\'{_bg_src(a, show, sheet, seg.background)}\')" '
             f'data-drift="{_win((seg.start, end))}"></div><div class="grade"></div><div class="stage">']
    for f in figs:
        imgs = "".join(f'<div class="pose" style="background-image:url(\'{_pose_src(a, show, members, f["name"], pose)}'
                       f'\')" data-show="{_win(*w)}"></div>' for pose, w in sorted(f["shows"].items()))
        lift = f' data-lift="{_win(*f["talk"])}"' if f["talk"] else ""
        parts.append(f'<div class="fig" style="left:{f["left"]}px;width:{f["width"]}px;height:{f["height"]}px"{lift}>'
                     f'{imgs}</div>')
    parts.append("</div>")
    for b in seg.bubbles:
        side = "chorus" if b.speaker not in where else ("l" if where[b.speaker] < W // 2 else "r")
        aim = ""
        if side != "chorus":  # the tail's offset from the bubble's near edge, so it points at the speaker
            cx = where[b.speaker]
            tail = max(TAIL_MIN, (cx - BUB_EDGE if side == "l" else W - BUB_EDGE - cx) - TAIL_HALF)
            aim = f";--tail:{tail}px;min-width:{tail + 2 * TAIL_HALF + TAIL_MIN}px"
        sticker = ""
        if b.sticker:
            emoji, label = STICKERS[b.sticker]
            sticker = f'<div class="stk" data-pop="{_win((rt.sticker_time(b), b.end))}">{emoji} [ {e(label)} ]</div>'
        parts.append(f'<div class="bub {side}" style="--c:{colour(b.speaker)}{aim}" data-pop="{_win((b.start, b.end))}">'
                     f'<span class="who txt">{e(b.speaker)}</span><span class="say txt">{e(b.text)}</span>'
                     f'{sticker}</div>')
    return "".join(parts)


def _previously(a: Assets, show: Path, seg: rt.Segment, show_title: str) -> str:
    end = seg.start + seg.length
    parts = ['<div class="brand"></div>']
    stills = seg.stills[:2]
    for k, rel in enumerate(stills):
        lo = seg.start + seg.length * k / len(stills)
        hi = seg.start + seg.length * (k + 1) / len(stills)
        src = a.add(show / rel, f"stills/{k}{Path(rel).suffix.lower() or '.jpg'}", inside=show,
                    what=f"'Previously on' still {rel}",
                    fix="The show's bible.json lists a still outside the show folder; restore bible.json from "
                        "the show's snapshots folder, then render again.")
        parts.append(f'<div class="shot" data-show="{_win((lo, hi))}"><div class="bg" '
                     f'style="background-image:url(\'{src}\')" data-drift="{_win((lo, hi))}"></div>'
                     '<div class="grade"></div></div>')
    parts.append(f'<div class="ph">Previously on</div><div class="pl txt">{e(show_title)}</div>')
    if seg.text:
        parts.append(f'<div class="pn txt" data-rise="{_win((seg.start + 0.6, end))}">{e(seg.text)}</div>')
    return "".join(parts)


def _title(a, show, sheet, tl: rt.Timeline, seg: rt.Segment, season: int, number: int) -> str:
    end = seg.start + seg.length
    first = tl.segment("cold_open").background
    return (f'<div class="bg" style="background-image:url(\'{_bg_src(a, show, sheet, first)}\')" '
            f'data-drift="{_win((seg.start, end))}"></div><div class="tw"></div>'
            f'<div class="tt txt" data-rise="{_win((seg.start + 0.15, end))}">{e(tl.show_title)}</div>'
            f'<div class="ts" data-rise="{_win((seg.start + 0.8, end))}">Season {season} · Episode {number}</div>'
            f'<div class="te txt" data-rise="{_win((seg.start + 1.2, end))}">“{e(tl.title)}”</div>')


def _credits(a: Assets, show: Path, members, seg: rt.Segment, colour) -> str:
    n = len(seg.cards)
    parts = []
    for k, card in enumerate(seg.cards):
        lo = seg.start + seg.length * k / n
        hi = seg.start + seg.length * (k + 1) / n
        rows = []
        for c in card:
            m = members.get(c["name"])
            if m and "neutral" in m[1]["poses"]:
                pic = f'<div class="pose" style="background-image:url(\'{_pose_src(a, show, members, c["name"], "neutral")}\')"></div>'
            else:
                pic = f'<div class="ini txt" style="--c:{colour(c["name"])}">{e(c["name"][:1].upper())}</div>'
            rows.append(f'<div class="cr"><div class="cp">{pic}</div><div><div class="cn txt">{e(c["name"])}</div>'
                        f'<div class="ca">as</div><div class="cl txt">{e(c["role"])}</div></div></div>')
        parts.append(f'<div class="card" data-slide="{_win((lo + 0.05, hi))}"><div class="cs">Starring</div>'
                     f'{"".join(rows)}</div>')
    return "".join(parts)


def _guide(tl: rt.Timeline, seg: rt.Segment, season: int, number: int) -> str:
    return (f'<div class="gc" data-rise="{_win((seg.start + 0.05, seg.start + seg.length))}">'
            '<div class="gh"><span>TONIGHT</span><span>8:00 PM</span></div>'
            f'<div class="gt txt">{e(tl.show_title)}</div>'
            f'<div class="ge txt">Season {season}, Episode {number} · “{e(tl.title)}”</div>'
            f'<div class="gd txt">{e(seg.text)}</div></div>')


def _renewal(a, show, sheet, tl: rt.Timeline, seg: rt.Segment, season: int) -> str:
    end = seg.start + seg.length
    last = tl.segment("scene_3").background
    return (f'<div class="bg" style="background-image:url(\'{_bg_src(a, show, sheet, last)}\')"></div>'
            f'<div class="dim"></div><div class="st" data-stamp="{_win((seg.start + 0.2, end))}">'
            f'<div class="s1">RENEWED</div><div class="s2">for Season {season + 1}</div></div>'
            f'<div class="mk" data-rise="{_win((seg.start + 1.0, end))}">made with <b>CHATCOM</b></div>')


def _fonts(a: Assets, big_fonts: Path) -> str:
    shipped = resources.files("chatcom") / "assets" / "fonts"
    rules = []
    for family, name, weight, style in SMALL_FONTS:
        with resources.as_file(shipped / name) as src:
            url = a.add(src, f"fonts/{name}", inside=None, what=f"font {name}",
                        fix="Reinstall chatcom; the package is missing a font.")
        rules.append(f"@font-face{{font-family:'{family}';src:url('{url}');font-weight:{weight};"
                     f"font-style:{style}}}")
    for family, name in BIG_FONTS:
        url = a.add(big_fonts / name, f"fonts/{name.replace('[wght]', '-wght')}", inside=None,
                    what=f"font {name}", fix="Run `chatcom doctor --install` to download the render fonts.")
        rules.append(f"@font-face{{font-family:'{family}';src:url('{url}')}}")
    return "\n".join(rules)


def write_project(tl: rt.Timeline, sheet: dict, show_dir: Path, build: Path, *, big_fonts: Path) -> Path:
    """Write the project into `build` (emptied first) and return index.html."""
    if build.exists():
        shutil.rmtree(build)
    build.mkdir(parents=True)
    a = Assets(build)
    members = _members(sheet)
    order = {m["name"]: i for i, m in enumerate(sheet["members"])}
    colour = lambda name: PALETTE[order.get(name, len(order)) % len(PALETTE)]  # noqa: E731
    season, number = (int(x) for x in tl.episode[1:].split("E"))
    sections = []
    for seg in tl.segments:
        if seg.name in rt.SCENE_SEGMENTS:
            body, cls = _scene(a, show_dir, sheet, members, seg, colour), "scene"
        elif seg.name == "previously_on":
            body, cls = _previously(a, show_dir, seg, tl.show_title), "prev"
        elif seg.name == "title_card":
            body, cls = _title(a, show_dir, sheet, tl, seg, season, number), "title"
        elif seg.name == "credits":
            body, cls = _credits(a, show_dir, members, seg, colour), "credits"
        elif seg.name == "tv_guide":
            body, cls = _guide(tl, seg, season, number), "guide"
        elif seg.name == "renewal":
            body, cls = _renewal(a, show_dir, sheet, tl, seg, season), "renewal"
        else:
            raise ValueError(f"no layout for segment {seg.name}")
        sections.append(f'<section id="sg-{seg.name}" class="seg {cls} clip" data-start="{_t(seg.start)}" '
                        f'data-duration="{_t(seg.length)}" data-track-index="1">{body}</section>')
    tpl = resources.files("chatcom") / "render_templates"
    page = (
        "<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"UTF-8\" />\n"
        f'<meta http-equiv="Content-Security-Policy" content="{CSP}" />\n'
        f'<meta name="viewport" content="width={W}, height={H}" />\n'
        f"<title>{e(tl.show_title)} {e(tl.episode)}</title>\n"
        f"<style>\n{_fonts(a, big_fonts)}\n{(tpl / 'episode.css').read_text('utf-8')}</style>\n</head>\n<body>\n"
        f'<div id="root" data-composition-id="main" data-start="0" data-duration="{_t(tl.total)}" '
        f'data-width="{W}" data-height="{H}" data-no-timeline>\n' + "\n".join(sections) + "\n</div>\n"
        f"<script>\n{(tpl / 'motion.js').read_text('utf-8')}</script>\n</body>\n</html>\n")
    index = build / "index.html"
    index.write_text(page, "utf-8")
    return index
