#!/usr/bin/env python3
"""Overlap / overflow checker for the .drawio files under docs/architecture/diagrams.

Checks every page (<diagram>) of each file:

1. well-formed XML, one <mxGraphModel> per <diagram>, unique cell ids, edges reference existing vertices;
2. vertices: two vertices either nest (container fully holds the other) or are disjoint — partial overlap is an
   error; a vertex inside a labelled zone must sit below the zone's title band;
3. text overflow: the label (html, <br>, <b>, font-size spans) is wrapped to the box width with a conservative
   Helvetica width estimate; the wrapped height must fit the box;
4. edges with explicit exit/entry points: every orthogonal segment is checked against every vertex that is not
   the edge's source, target or a container (zone) — an edge may not run through a box. Free edges (no
   source/target, absolute sourcePoint/targetPoint, the sequence-diagram style of 02–04) are checked the same way;
   a label with verticalAlign=bottom is placed above its line, as draw.io draws it;
5. edge labels: the label box (estimated) at its position along the path must not overlap a box or another
   edge's label.

Only geometry that is absolute (parent="1") is supported, which is what the Ask the Tower generators write.

    uv run python docs/architecture/diagrams/gen/check.py docs/architecture/diagrams/08-agentcore-deployment.drawio
"""

from __future__ import annotations

import html
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

CHAR_W = 0.56  # average glyph width / font size, regular (conservative for Helvetica)
CHAR_W_BOLD = 0.62
LINE_H = 1.22
ZONE_TITLE_BAND = 26


@dataclass
class V:
    id: str
    x: float
    y: float
    w: float
    h: float
    value: str
    style: dict[str, str]

    @property
    def zone(self) -> bool:
        return self.style.get("verticalAlign") == "top" and not self.is_text

    @property
    def is_text(self) -> bool:
        return "text" in self.style

    def contains(self, o: V) -> bool:
        return self.x <= o.x and self.y <= o.y and o.x + o.w <= self.x + self.w and o.y + o.h <= self.y + self.h

    def intersects(self, o: V | tuple[float, float, float, float], pad: float = 0) -> bool:
        x, y, w, h = (o.x, o.y, o.w, o.h) if isinstance(o, V) else o
        return x < self.x + self.w - pad and self.x + pad < x + w and y < self.y + self.h - pad and self.y + pad < y + h


@dataclass
class E:
    id: str
    src: str
    tgt: str
    value: str
    style: dict[str, str]
    points: list[tuple[float, float]] = field(default_factory=list)
    rel_x: float = 0.0
    offset: tuple[float, float] = (0.0, 0.0)
    src_pt: tuple[float, float] | None = None
    tgt_pt: tuple[float, float] | None = None


def parse_style(s: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in s.split(";"):
        if not part:
            continue
        k, _, v = part.partition("=")
        out[k] = v
    return out


class _Lines(HTMLParser):
    """html label -> [(text, font_size, bold)] per hard line."""

    def __init__(self, size: float, bold: bool) -> None:
        super().__init__()
        self.stack: list[tuple[float, bool]] = [(size, bold)]
        self.lines: list[list[tuple[str, float, bool]]] = [[]]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        size, bold = self.stack[-1]
        if tag == "br":
            self.lines.append([])
            return
        if tag in ("b", "strong"):
            bold = True
        st = dict(attrs).get("style") or ""
        m = re.search(r"font-size:\s*([\d.]+)px", st)
        if m:
            size = float(m.group(1))
        if tag in ("div", "p") and self.lines[-1]:
            self.lines.append([])
        if tag != "br":
            self.stack.append((size, bold))

    def handle_endtag(self, tag: str) -> None:
        if tag != "br" and len(self.stack) > 1:
            self.stack.pop()

    def handle_data(self, data: str) -> None:
        size, bold = self.stack[-1]
        parts = data.split("\n")
        for i, p in enumerate(parts):
            if i:
                self.lines.append([])
            if p:
                self.lines[-1].append((p, size, bold))


def text_extent(value: str, style: dict[str, str], width: float | None) -> tuple[float, float]:
    """(max line width, total height) of a label wrapped at `width` (None = no wrapping)."""
    size = float(style.get("fontSize", "11"))
    bold = style.get("fontStyle", "0") in ("1", "3", "5", "7")
    p = _Lines(size, bold)
    p.feed(value if style.get("html") == "1" else html.escape(value).replace("\n", "<br>"))
    total_h = 0.0
    max_w = 0.0
    for line in p.lines:
        if not line:
            total_h += size * LINE_H
            continue
        lh = max(s for _, s, _ in line) * LINE_H
        words: list[tuple[float, float]] = []  # (width, size) per word incl. trailing space
        for txt, s, b in line:
            cw = (CHAR_W_BOLD if b else CHAR_W) * s
            for word in re.findall(r"\S+\s*|\s+", txt):
                words.append((len(word) * cw, s))
        cur = 0.0
        n = 1
        for ww, _ in words:
            if width is not None and cur > 0 and cur + ww > width:
                max_w = max(max_w, cur)
                n += 1
                cur = ww
            else:
                cur += ww
        max_w = max(max_w, cur)
        total_h += n * lh
    return max_w, total_h


def load(path: Path) -> list[tuple[str, dict[str, V], list[E]]]:
    tree = ET.parse(path)
    root = tree.getroot()
    if root.tag != "mxfile":
        raise SystemExit(f"{path}: root is {root.tag}, not mxfile")
    pages = []
    for d in root.findall("diagram"):
        models = d.findall("mxGraphModel")
        if len(models) != 1:
            raise SystemExit(f"{path}: page {d.get('name')}: expected one mxGraphModel (uncompressed)")
        vs: dict[str, V] = {}
        es: list[E] = []
        ids: set[str] = set()
        for c in models[0].iter("mxCell"):
            cid = c.get("id", "")
            if cid in ids:
                raise SystemExit(f"{path}: page {d.get('name')}: duplicate id {cid}")
            ids.add(cid)
            g = c.find("mxGeometry")
            st = parse_style(c.get("style", ""))
            if c.get("vertex") == "1" and g is not None:
                vs[cid] = V(
                    cid,
                    float(g.get("x", 0)),
                    float(g.get("y", 0)),
                    float(g.get("width", 0)),
                    float(g.get("height", 0)),
                    c.get("value", ""),
                    st,
                )
            elif c.get("edge") == "1":
                e = E(cid, c.get("source", ""), c.get("target", ""), c.get("value", ""), st)
                if g is not None:
                    e.rel_x = float(g.get("x", 0))
                    arr = g.find("Array")
                    if arr is not None:
                        e.points = [(float(p.get("x", 0)), float(p.get("y", 0))) for p in arr.findall("mxPoint")]
                    off = [p for p in g.findall("mxPoint") if p.get("as") == "offset"]
                    if off:
                        e.offset = (float(off[0].get("x", 0)), float(off[0].get("y", 0)))
                    for pt in g.findall("mxPoint"):
                        xy = (float(pt.get("x", 0)), float(pt.get("y", 0)))
                        if pt.get("as") == "sourcePoint":
                            e.src_pt = xy
                        elif pt.get("as") == "targetPoint":
                            e.tgt_pt = xy
                es.append(e)
        pages.append((d.get("name", "?"), vs, es))
    return pages


def is_free(e: E) -> bool:
    """A free edge: no source/target vertex, both ends given as absolute points."""
    return not e.src and not e.tgt and e.src_pt is not None and e.tgt_pt is not None


def edge_path(e: E, vs: dict[str, V]) -> list[tuple[float, float]] | None:
    if is_free(e):
        assert e.src_pt is not None and e.tgt_pt is not None
        return [e.src_pt, *e.points, e.tgt_pt]
    s, t = vs.get(e.src), vs.get(e.tgt)
    if s is None or t is None or "exitX" not in e.style or "entryX" not in e.style:
        return None
    p0 = (s.x + float(e.style["exitX"]) * s.w, s.y + float(e.style["exitY"]) * s.h)
    p1 = (t.x + float(e.style["entryX"]) * t.w, t.y + float(e.style["entryY"]) * t.h)
    return [p0, *e.points, p1]


def point_along(path: list[tuple[float, float]], frac: float) -> tuple[float, float]:
    segs = [(a, b, abs(b[0] - a[0]) + abs(b[1] - a[1])) for a, b in zip(path, path[1:], strict=False)]
    total = sum(L for _, _, L in segs) or 1.0
    want = frac * total
    for a, b, L in segs:
        if want <= L and L > 0:
            r = want / L
            return (a[0] + (b[0] - a[0]) * r, a[1] + (b[1] - a[1]) * r)
        want -= L
    return path[-1]


def check(path: Path) -> list[str]:
    issues: list[str] = []
    for name, vs, es in load(path):
        where = f"{path.name} [{name}]"
        boxes = list(vs.values())
        # 2. overlap and title band
        for i, a in enumerate(boxes):
            for b in boxes[i + 1 :]:
                if not a.intersects(b):
                    continue
                if a.contains(b) or b.contains(a):
                    outer, inner = (a, b) if a.contains(b) else (b, a)
                    if not (outer.zone or outer.is_text) and not inner.is_text:
                        issues.append(f"{where}: box {inner.id} sits inside non-zone box {outer.id}")
                    elif outer.zone and outer.value.strip() and inner.y < outer.y + ZONE_TITLE_BAND:
                        tw, _ = text_extent(outer.value, outer.style, None)
                        if inner.x < outer.x + 12 + tw:
                            issues.append(f"{where}: {inner.id} overlaps the title band of zone {outer.id}")
                    continue
                issues.append(f"{where}: {a.id} and {b.id} partially overlap")
        # 3. text overflow
        for v in boxes:
            if not v.value.strip():
                continue
            pad_l = float(v.style.get("spacingLeft", 0)) + float(v.style.get("spacing", 4))
            avail_w = v.w - 2 * pad_l - 4
            if v.zone:
                tw, _ = text_extent(v.value, v.style, None)
                if tw > avail_w:
                    issues.append(f"{where}: zone title of {v.id} wider than the zone ({tw:.0f} > {avail_w:.0f})")
                continue
            if v.style.get("whiteSpace") != "wrap":
                tw, th = text_extent(v.value, v.style, None)
                if tw > avail_w:
                    issues.append(f"{where}: {v.id} text wider than box ({tw:.0f} > {avail_w:.0f}), no wrap")
            _, th = text_extent(v.value, v.style, avail_w)
            if th > v.h - 4:
                issues.append(f"{where}: {v.id} text overflows ({th:.0f} > {v.h - 4:.0f} px tall)")
        # 4. edges through boxes, 5. labels
        label_boxes: list[tuple[str, tuple[float, float, float, float]]] = []
        for e in es:
            if not is_free(e) and (e.src not in vs or e.tgt not in vs):
                issues.append(f"{where}: edge {e.id} references a missing vertex")
                continue
            p = edge_path(e, vs)
            if p is None:
                continue
            for a, b in zip(p, p[1:], strict=False):
                if abs(a[0] - b[0]) > 0.5 and abs(a[1] - b[1]) > 0.5:
                    issues.append(f"{where}: edge {e.id} has a diagonal segment {a}->{b}; add the corner point")
                seg = (min(a[0], b[0]) - 1, min(a[1], b[1]) - 1, abs(a[0] - b[0]) + 2, abs(a[1] - b[1]) + 2)
                for v in boxes:
                    if v.id in (e.src, e.tgt) or v.is_text:
                        continue
                    if v.zone:
                        if v.value.strip():  # the zone's title text is a no-go area for edges
                            tw, _ = text_extent(v.value, v.style, None)
                            band = V("band", v.x + 6, v.y + 2, tw + 14, ZONE_TITLE_BAND - 4, "", {})
                            if band.intersects(seg):
                                issues.append(f"{where}: edge {e.id} crosses the title of zone {v.id}")
                        continue
                    if v.intersects(seg, pad=1):
                        issues.append(f"{where}: edge {e.id} runs through {v.id}")
            if e.value.strip():
                frac = (e.rel_x + 1) / 2
                cx, cy = point_along(p, frac)
                cx += e.offset[0]
                cy += e.offset[1]
                lw, lh = text_extent(e.value, {**e.style, "html": "1"}, None)
                if e.style.get("verticalAlign") == "bottom":
                    cy -= lh / 2 + 1  # draw.io puts a bottom-aligned edge label above the line
                lb = (cx - lw / 2 - 2, cy - lh / 2 - 1, lw + 4, lh + 2)
                for v in boxes:
                    if v.zone:
                        continue
                    if v.intersects(lb):
                        issues.append(f"{where}: label of {e.id} overlaps {v.id}")
                for oid, ob in label_boxes:
                    x, y, w, h = lb
                    ox, oy, ow, oh = ob
                    if x < ox + ow and ox < x + w and y < oy + oh and oy < y + h:
                        issues.append(f"{where}: label of {e.id} overlaps label of {oid}")
                label_boxes.append((e.id, lb))
    return issues


def main(argv: list[str]) -> int:
    files = [Path(a) for a in argv] or sorted(Path(__file__).resolve().parents[1].glob("*.drawio"))
    bad = 0
    for f in files:
        issues = check(f)
        pages = len(load(f))
        if issues:
            bad += len(issues)
            print(f"{f.name}: {len(issues)} issue(s) on {pages} page(s)")
            for i in issues:
                print("  " + i)
        else:
            print(f"{f.name}: ok ({pages} page(s))")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
