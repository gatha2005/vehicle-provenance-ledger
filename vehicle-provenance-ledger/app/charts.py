"""Tiny server-side SVG charts. No JavaScript, no external libraries, works offline."""
from __future__ import annotations

import html
import math
import time

TYPE_CLASS = {"REGISTRATION": "c-reg", "SERVICE": "c-svc", "ACCIDENT": "c-acc", "OWNERSHIP_TRANSFER": "c-xfer",
              "STATUS_CHANGE": "c-stolen", "LIEN_PLACED": "c-lien", "LIEN_RELEASED": "c-lien"}
TYPE_LABEL = {"REGISTRATION": "Registration", "SERVICE": "Service", "ACCIDENT": "Accident",
              "OWNERSHIP_TRANSFER": "Transfer", "STATUS_CHANGE": "Stolen flag", "LIEN_PLACED": "Lien placed",
              "LIEN_RELEASED": "Lien released"}
e = html.escape


def _nice_max(v: float) -> float:
    if v <= 0:
        return 4
    if v <= 40:
        return max(4, math.ceil(v / 4) * 4)      # ticks at quarters stay whole numbers
    mag = 10 ** math.floor(math.log10(v))
    for m in (1, 2, 5, 10):
        if v <= m * mag:
            return m * mag
    return 10 * mag


def bar(items: list[tuple[str, float]], cls: str = "c-svc", w: int = 520, h: int = 220, title: str = "") -> str:
    if not items:
        return '<p class="empty">No data yet.</p>'
    top = _nice_max(max(v for _, v in items)); pl, pb, pt = 34, 34, 14
    bw = (w - pl - 10) / len(items)
    out = [f'<svg class="chart" viewBox="0 0 {w} {h}" role="img" aria-label="{e(title)}">']
    for i in range(5):
        y = pt + (h - pb - pt) * i / 4
        out.append(f'<line class="grid" x1="{pl}" x2="{w - 6}" y1="{y:.1f}" y2="{y:.1f}"/>'
                   f'<text class="ax" x="{pl - 6}" y="{y + 4:.1f}" text-anchor="end">{top * (4 - i) / 4:g}</text>')
    for i, (lab, v) in enumerate(items):
        bh = (h - pb - pt) * v / top; x = pl + i * bw + bw * 0.18
        out.append(f'<rect class="{cls}" x="{x:.1f}" y="{h - pb - bh:.1f}" width="{bw * 0.64:.1f}" height="{bh:.1f}" rx="4">'
                   f'<title>{e(lab)}: {v:g}</title></rect>'
                   f'<text class="val" x="{x + bw * 0.32:.1f}" y="{h - pb - bh - 5:.1f}" text-anchor="middle">{v:g}</text>'
                   f'<text class="ax" x="{x + bw * 0.32:.1f}" y="{h - 14}" text-anchor="middle">{e(lab)}</text>')
    return "".join(out) + "</svg>"


def hbar(items: list[tuple[str, float]], cls: str = "c-acc", w: int = 520, row: int = 30) -> str:
    if not items:
        return '<p class="empty">Nothing recorded yet.</p>'
    top = max(v for _, v in items) or 1; lw = 150; h = row * len(items) + 6
    out = [f'<svg class="chart" viewBox="0 0 {w} {h}" role="img" aria-label="Horizontal bar chart">']
    for i, (lab, v) in enumerate(items):
        y = 4 + i * row; bw = (w - lw - 50) * v / top
        out.append(f'<text class="ax" x="0" y="{y + 17}">{e(lab)}</text>'
                   f'<rect class="{cls}" x="{lw}" y="{y + 3}" width="{max(bw, 3):.1f}" height="{row - 10}" rx="4"/>'
                   f'<text class="val" x="{lw + bw + 8:.1f}" y="{y + 17}">{v:g}</text>')
    return "".join(out) + "</svg>"


def line(points: list[tuple[str, float]], w: int = 520, h: int = 220, title: str = "") -> str:
    if len(points) < 2:
        return bar(points, "c-reg", w, h, title)
    top = _nice_max(max(v for _, v in points)); pl, pb, pt, pr = 34, 34, 14, 12
    n = len(points); xs = lambda i: pl + (w - pl - pr) * i / (n - 1)
    ys = lambda v: pt + (h - pb - pt) * (1 - v / top)
    out = [f'<svg class="chart" viewBox="0 0 {w} {h}" role="img" aria-label="{e(title)}">']
    for i in range(5):
        y = pt + (h - pb - pt) * i / 4
        out.append(f'<line class="grid" x1="{pl}" x2="{w - pr}" y1="{y:.1f}" y2="{y:.1f}"/>'
                   f'<text class="ax" x="{pl - 6}" y="{y + 4:.1f}" text-anchor="end">{top * (4 - i) / 4:g}</text>')
    pts = " ".join(f"{xs(i):.1f},{ys(v):.1f}" for i, (_, v) in enumerate(points))
    out.append(f'<polygon class="area" points="{pl},{h - pb} {pts} {w - pr},{h - pb}"/><polyline class="ln" points="{pts}"/>')
    step = max(1, n // 6)
    for i, (lab, v) in enumerate(points):
        out.append(f'<circle class="dot" cx="{xs(i):.1f}" cy="{ys(v):.1f}" r="3.5"><title>{e(lab)}: {v:g}</title></circle>')
        if i % step == 0 or i == n - 1:
            out.append(f'<text class="ax" x="{xs(i):.1f}" y="{h - 14}" text-anchor="{"end" if i == n - 1 else "middle"}">{e(lab)}</text>')
    return "".join(out) + "</svg>"


def donut(items: list[tuple[str, float, str]], size: int = 190) -> str:
    total = sum(v for _, v, _ in items) or 1; r, sw, c = 70, 26, size / 2; circ = 2 * math.pi * r
    out = [f'<svg class="donut" viewBox="0 0 {size} {size}" role="img" aria-label="Distribution chart">']
    off = 0.0
    for lab, v, cls in items:
        d = circ * v / total
        out.append(f'<circle class="{cls}" cx="{c}" cy="{c}" r="{r}" fill="none" stroke-width="{sw}" '
                   f'stroke-dasharray="{d:.2f} {circ - d:.2f}" stroke-dashoffset="{-off:.2f}" transform="rotate(-90 {c} {c})">'
                   f'<title>{e(lab)}: {v:g}</title></circle>')
        off += d
    out.append(f'<text class="big" x="{c}" y="{c + 6}" text-anchor="middle">{total:g}</text></svg>')
    return "".join(out)


def gauge(score: int, grade: str) -> str:
    """Semicircle gauge. Colour comes from the grade class so it follows the theme."""
    r, cx, cy = 80, 100, 100; circ = math.pi * r
    return (f'<svg class="gauge g-{grade}" viewBox="0 0 200 124" role="img" aria-label="Trust score {score} out of 100, grade {grade}">'
            f'<path class="track" d="M{cx - r},{cy} A{r},{r} 0 0 1 {cx + r},{cy}" fill="none" stroke-width="16" stroke-linecap="round"/>'
            f'<path class="fill" d="M{cx - r},{cy} A{r},{r} 0 0 1 {cx + r},{cy}" fill="none" stroke-width="16" stroke-linecap="round" '
            f'stroke-dasharray="{circ * score / 100:.1f} {circ:.1f}"/>'
            f'<text class="score" x="{cx}" y="{cy - 6}" text-anchor="middle">{score}</text>'
            f'<text class="grade" x="{cx}" y="{cy + 18}" text-anchor="middle">Grade {grade}</text></svg>')


def odometer(history: list[dict], anomaly_idx: set[int], w: int = 640, h: int = 260) -> str:
    pts = [(x["timestamp"], x["odometer"], x) for x in history]
    if len(pts) < 2:
        return '<p class="empty">Not enough records to draw a chart.</p>'
    t0, t1 = pts[0][0], pts[-1][0]; span_t = max(t1 - t0, 1)
    top = _nice_max(max(p[1] for p in pts)); pl, pb, pt, pr = 52, 36, 14, 16
    X = lambda t: pl + (w - pl - pr) * (t - t0) / span_t
    Y = lambda o: pt + (h - pb - pt) * (1 - o / top)
    out = [f'<svg class="chart" viewBox="0 0 {w} {h}" role="img" aria-label="Odometer readings over time">']
    for i in range(5):
        y = pt + (h - pb - pt) * i / 4
        out.append(f'<line class="grid" x1="{pl}" x2="{w - pr}" y1="{y:.1f}" y2="{y:.1f}"/>'
                   f'<text class="ax" x="{pl - 6}" y="{y + 4:.1f}" text-anchor="end">{int(top * (4 - i) / 4):,}</text>')
    poly = " ".join(f"{X(t):.1f},{Y(o):.1f}" for t, o, _ in pts)
    out.append(f'<polygon class="area" points="{pl},{h - pb} {poly} {X(t1):.1f},{h - pb}"/><polyline class="ln" points="{poly}"/>')
    for t, o, x in pts:
        ring = ' <circle class="ring" cx="{:.1f}" cy="{:.1f}" r="9"/>'.format(X(t), Y(o)) if x["index"] in anomaly_idx else ""
        out.append(f'{ring}<circle class="{TYPE_CLASS.get(x["type"], "dot")} mk" cx="{X(t):.1f}" cy="{Y(o):.1f}" r="5.5">'
                   f'<title>{e(TYPE_LABEL.get(x["type"], x["type"]))}: {o:,} km ({time.strftime("%d %b %Y", time.gmtime(t))})</title></circle>')
    for frac in (0, .5, 1):
        t = t0 + span_t * frac
        out.append(f'<text class="ax" x="{X(t):.1f}" y="{h - 12}" text-anchor="{"start" if frac == 0 else "end" if frac == 1 else "middle"}">'
                   f'{time.strftime("%b %Y", time.gmtime(t))}</text>')
    return "".join(out) + "</svg>"
