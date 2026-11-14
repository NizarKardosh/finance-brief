"""Render data/<date>/*.json into reports/<date>.html

Usage: python render.py [YYYY-MM-DD]
"""
from __future__ import annotations

import datetime as dt
import html
import json
import math
import sys
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).resolve().parent


def load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


# ---------------------------------------------------------------- formatting
def pct(v, digits=2):
    if v is None:
        return "—"
    return f"{'+' if v > 0 else ''}{v:.{digits}f}%"


def price(v):
    if v is None:
        return "—"
    if abs(v) >= 1000:
        return f"{v:,.0f}" if abs(v) >= 10000 else f"{v:,.2f}"
    return f"{v:,.2f}"


def money(v):
    if not v:
        return "—"
    for div, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(v) >= div:
            return f"${v / div:.1f}{suf}" if div < 1e12 else f"${v / div:.2f}{suf}"
    return f"${v:.0f}"


def tone(v):
    if v is None or abs(v) < 0.005:
        return "flat"
    return "up" if v > 0 else "down"


def reltime(ts, now):
    if not ts:
        return ""
    try:
        t = dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=now.tzinfo)
        mins = int((now - t).total_seconds() // 60)
    except Exception:
        return ""
    if mins < 0:
        return "upcoming"
    if mins < 60:
        return f"{max(mins, 1)}m ago"
    if mins < 48 * 60:
        return f"{mins // 60}h ago"
    return f"{mins // 1440}d ago"


# ---------------------------------------------------------------- treemap
def _worst(row, w):
    s = sum(row)
    if s == 0 or w == 0:
        return math.inf
    return max(max(w * w * r / (s * s), (s * s) / (w * w * r)) for r in row)


def squarify(values, x, y, w, h):
    """Return list of (x, y, w, h) for values (already sorted desc), filling the rect."""
    total = sum(values)
    if total <= 0 or not values:
        return [(x, y, 0, 0) for _ in values]
    scaled = [v * w * h / total for v in values]
    rects, row, i = [], [], 0
    while i < len(scaled):
        side = min(w, h)
        cand = row + [scaled[i]]
        if not row or _worst(cand, side) <= _worst(row, side):
            row = cand
            i += 1
            continue
        x, y, w, h = _layout_row(row, x, y, w, h, rects)
        row = []
    if row:
        _layout_row(row, x, y, w, h, rects)
    return rects


def _layout_row(row, x, y, w, h, rects):
    s = sum(row)
    if w >= h:  # lay out a column on the left
        cw = s / h if h else 0
        cy = y
        for r in row:
            rh = r / cw if cw else 0
            rects.append((x, cy, cw, rh))
            cy += rh
        return x + cw, y, w - cw, h
    rh = s / w if w else 0
    cx = x
    for r in row:
        rw = r / rh if rh else 0
        rects.append((cx, y, rw, rh))
        cx += rw
    return x, y + rh, w, h - rh


def heat_color(c):
    """Diverging palette: red (-3%) -> slate (0) -> green (+3%)."""
    c = max(-3.0, min(3.0, c or 0.0)) / 3.0
    neutral = (58, 63, 76)
    up, down = (16, 160, 116), (226, 60, 72)
    tgt = up if c > 0 else down
    k = abs(c) ** 0.8
    r, g, b = (round(n + (t - n) * k) for n, t in zip(neutral, tgt))
    return f"rgb({r},{g},{b})"


def build_heatmap(sectors, W=1200, H=680, max_per_sector=60):
    sectors = [s for s in sectors if s.get("market_cap")]
    if not sectors:
        return ""
    srects = squarify([s["market_cap"] for s in sectors], 0, 0, W, H)
    out = [f'<svg class="heatmap" viewBox="0 0 {W} {H}" preserveAspectRatio="none" role="img" '
           f'aria-label="S&amp;P 500 heatmap by sector, sized by market cap, colored by daily change">']
    for sec, (sx, sy, sw, sh) in zip(sectors, srects):
        pad, head = 1.5, (17 if sh > 60 and sw > 70 else 0)
        stocks = sorted([s for s in sec["stocks"] if s.get("m")], key=lambda s: -s["m"])[:max_per_sector]
        inner = squarify([s["m"] for s in stocks], sx + pad, sy + pad + head, sw - 2 * pad, sh - 2 * pad - head)
        out.append(f'<g class="sec"><rect class="sec-bg" x="{sx:.1f}" y="{sy:.1f}" width="{sw:.1f}" height="{sh:.1f}"/>')
        if head:
            label = html.escape(sec["name"].upper())
            max_chars = int((sw - 50) / 6.2)
            if len(label) > max_chars:
                label = label[: max(max_chars - 1, 3)] + "…"
            out.append(f'<text class="sec-label" x="{sx + 6:.1f}" y="{sy + 12.5:.1f}">{label}'
                       f'<tspan class="{tone(sec["change"])}" dx="6">{pct(sec["change"])}</tspan></text>')
        for st, (x, y, w, h) in zip(stocks, inner):
            if w < 1 or h < 1:
                continue
            tip = html.escape(f'{st["t"]} · {st["n"]}\n{pct(st["c"])}  ·  ${price(st["p"])}  ·  {money(st["m"])}')
            out.append(f'<g class="cell"><title>{tip}</title>'
                       f'<rect x="{x + .5:.1f}" y="{y + .5:.1f}" width="{max(w - 1, 0):.1f}" height="{max(h - 1, 0):.1f}" '
                       f'fill="{heat_color(st["c"])}"/>')
            area = w * h
            if w > 30 and h > 18:
                fs = max(9, min(26, math.sqrt(area) / 4.2))
                cx, cy = x + w / 2, y + h / 2
                show_pct = h > fs * 2.4 and w > fs * 2.6
                ty = cy - (fs * .25 if show_pct else -fs * .35)
                out.append(f'<text class="tk" x="{cx:.1f}" y="{ty:.1f}" font-size="{fs:.1f}">{html.escape(st["t"])}</text>')
                if show_pct:
                    out.append(f'<text class="tp" x="{cx:.1f}" y="{ty + fs * .95:.1f}" font-size="{fs * .62:.1f}">{pct(st["c"])}</text>')
            out.append("</g>")
        out.append("</g>")
    out.append("</svg>")
    return "".join(out)


# ---------------------------------------------------------------- main
def archive_neighbors(date):
    days = sorted(p.stem for p in (ROOT / "reports").glob("????-??-??.html"))
    if date not in days:
        days = sorted(days + [date])
    i = days.index(date)
    return (days[i - 1] if i > 0 else None), (days[i + 1] if i + 1 < len(days) else None)


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    artifact = "--artifact" in sys.argv
    date = args[0] if args else dt.date.today().isoformat()
    ddir = ROOT / "data" / date
    markets = load(ddir / "markets.json") or {}
    brief = load(ddir / "briefing.json")
    pulse = load(ddir / "pulse.json") or {}

    # Fallback: no briefing (news step failed) -> build a lean one from whatever scout files exist
    news_ok = bool(brief and brief.get("stories"))
    if not news_ok:
        stories = []
        for key, src in (("bloomberg", "Bloomberg"), ("yahoo", "Yahoo Finance"), ("google", "Google Finance")):
            for s in (load(ddir / f"news_{key}.json") or {}).get("stories", []):
                s.setdefault("sources", [src])
                s.setdefault("links", [{"source": src, "url": s.get("url")}])
                stories.append(s)
        stories.sort(key=lambda s: -(s.get("importance") or 0))
        brief = {"stories": stories[:12], "market_pulse": pulse.get("pulse", ""),
                 "standouts": pulse.get("standouts", []), "big_picture": pulse.get("pulse", ""),
                 "three_things": [], "watch_today": [], "headline_of_day": ""}

    # quote lookup for ticker chips
    quotes = {}
    for s in (markets.get("heatmap") or []):
        for st in s["stocks"]:
            quotes[st["t"]] = st["c"]
    for k in ("most_active", "gainers", "losers"):
        for st in markets.get(k) or []:
            quotes.setdefault(st["ticker"], st["change"])
    for g in markets.get("watchlist") or []:
        for st in g["items"]:
            if not st.get("missing"):
                quotes.setdefault(st["ticker"], st["change"])

    now = dt.datetime.now().astimezone()
    d = dt.date.fromisoformat(date)
    prev_day, next_day = archive_neighbors(date)
    stories = brief.get("stories") or []

    env = Environment(loader=FileSystemLoader(ROOT / "templates"), autoescape=select_autoescape(["html", "j2"]))
    env.filters.update(pct=pct, price=price, money=money, tone=tone, rel=lambda ts: reltime(ts, now))
    tpl = env.get_template("report.html.j2")
    page = tpl.render(
        date=date,
        date_long=d.strftime("%A, %B ") + str(d.day) + d.strftime(", %Y"),
        issue_no=(d - dt.date(2026, 1, 1)).days + 1,
        generated=now.strftime("%H:%M"),
        m=markets,
        b=brief,
        lead=stories[0] if stories else None,
        stories=stories[1:],
        news_ok=news_ok,
        auth_error=bool((load(ddir / "status.json") or {}).get("auth_error")),
        quotes=quotes,
        heatmap_svg=build_heatmap(markets.get("heatmap") or []),
        prev_day=prev_day,
        next_day=next_day,
        artifact=artifact,
        max_heat=max([abs(s.get("change") or 0) for s in markets.get("sectors") or []] + [0.01]),
    )
    out = ROOT / "reports" / ("artifact/morning-brief.html" if artifact else f"{date}.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    # index.html always shows the newest edition (bookmarkable link for the web/phone)
    if not artifact and next_day is None:
        (ROOT / "reports" / "index.html").write_text(page, encoding="utf-8")
    print(f"OK {out} ({len(stories)} stories, news_ok={news_ok})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
