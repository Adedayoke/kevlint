"""Render a check result as one self-contained HTML file.

Everything is inline and the page's content security policy forbids network
access. Timelines are SVG built here, so they display without JavaScript; the
script only adds richer tooltips over the SVG <title> fallback.
"""
import html
import math

from .inversion import HOLDER_NOTE, TYPICAL_FIXES
from .patterns import group_patterns
from .timeline import BLOCKED, READY, RUNNING, thread_segments

WIDTH = 1000
LEFT = 176
RIGHT = 20
TOP = 36
LANE_HEIGHT = 34
LANE_GAP = 12
AXIS_HEIGHT = 26
MIN_MARK = 2
MIN_HIT = 12
BADGE_SPACING = 20
LABEL_CHARS = 20

CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
    "base-uri 'none'; form-action 'none'"
)

CSS = """
:root {
  color-scheme: light;
  --page: #f9f9f7; --surface: #fcfcfb; --ink: #0b0b0b; --ink-2: #52514e;
  --muted: #6f6d67; --grid: #e1e0d9; --axis: #c3c2b7;
  --border: rgba(11,11,11,.10); --code: #f0efec;
  --running: #2a78d6; --ready: #eda100; --blocked: #c22f2f;
  --ready-ink: #a96f00; --blocked-ink: #8d1f1f;
  --good: #0ca30c; --critical: #d03b3b; --warning: #fab219;
  --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
  --sans: system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
}
@media (prefers-color-scheme: dark) {
  :root {
    color-scheme: dark;
    --page: #0d0d0d; --surface: #1a1a19; --ink: #f0efec; --ink-2: #c3c2b7;
    --muted: #a3a198; --grid: #2c2c2a; --axis: #383835;
    --border: rgba(255,255,255,.10); --code: #2c2c2a;
    --running: #3987e5; --ready: #e0a412; --blocked: #c83636;
    --ready-ink: #9a6e00; --blocked-ink: #8a2222;
  }
}
* { box-sizing: border-box; }
[hidden] { display: none !important; }
body { margin: 0; background: var(--page); color: var(--ink);
  font: 14px/1.55 var(--sans); }
main { max-width: 1080px; margin: 0 auto; padding-block: 28px 48px;
  padding-inline: 20px; display: grid; gap: 16px; }
h1, h2, h3, p { margin: 0; }
code, .mono { font-family: var(--mono); font-size: .92em; }
.brand { display: flex; flex-wrap: wrap; gap: 4px 12px; align-items: baseline;
  color: var(--ink-2); }
.brand b { color: var(--ink); font: 600 15px var(--mono); }
.brand .path { font-family: var(--mono); font-size: 12.5px; overflow-wrap: anywhere; }
.card { background: var(--surface); border: 1px solid var(--border);
  border-radius: 10px; padding: 20px; min-width: 0; }
.banner { display: flex; flex-wrap: wrap; align-items: center; gap: 14px 18px;
  background: var(--surface); border: 1px solid var(--border);
  border-left: 6px solid var(--status); border-radius: 10px; padding: 20px; }
.banner.fail { --status: var(--critical); }
.banner.pass { --status: var(--good); }
.banner .icon { width: 42px; height: 42px; flex: none; border-radius: 50%;
  background: var(--status); color: #fff; font: 700 22px/42px var(--sans);
  text-align: center; }
.banner .verdict { font-size: 28px; font-weight: 700; line-height: 1.1;
  letter-spacing: .02em; }
.banner .summary { color: var(--ink-2); }
.banner .scope { margin-left: auto; text-align: right; color: var(--ink-2);
  font-variant-numeric: tabular-nums; }
.banner .scope b { color: var(--ink); }
.grid-2 { display: grid; gap: 16px; grid-template-columns: minmax(0, 3fr) minmax(0, 2fr); }
.eyebrow { font-size: 12px; font-weight: 600; letter-spacing: .06em;
  text-transform: uppercase; color: var(--muted); margin-bottom: 10px; }
.stats { display: flex; flex-wrap: wrap; gap: 8px 32px; font-variant-numeric: tabular-nums; }
.stat b { display: block; font-size: 26px; line-height: 1.2; }
.stat span { color: var(--ink-2); font-size: 13px; }
.meter { display: flex; gap: 2px; height: 8px; margin: 14px 0 12px; }
.meter i { display: block; background: var(--grid); border-radius: 2px; min-width: 2px; }
.meter i.done { background: var(--ink-2); }
.reasons { margin: 0; padding: 0; list-style: none; color: var(--ink-2); }
.reasons li { display: flex; gap: 10px; padding: 3px 0; }
.reasons b { min-width: 2.5em; text-align: right; color: var(--ink);
  font-variant-numeric: tabular-nums; }
.plain { color: var(--ink-2); }
.plain code { color: var(--ink); }
.note { color: var(--ink-2); }
.note strong { color: var(--ink); }
.pattern { display: grid; gap: 14px; }
.pattern h3 { font-size: 16px; font-weight: 600; line-height: 1.4; }
.pattern h3 em { font-style: normal; color: var(--ink-2); font-weight: 400; }
.chips { display: flex; flex-wrap: wrap; gap: 8px; }
.chip { padding: 2px 10px; border: 1px solid var(--border); border-radius: 999px;
  color: var(--ink-2); font-size: 13px; font-variant-numeric: tabular-nums; }
.chip b { color: var(--ink); }
.fix { padding: 10px 12px; background: var(--code); border-radius: 6px; }
.fix strong { margin-right: 6px; }
.scroll { overflow-x: auto; }
svg.timeline { display: block; width: 100%; min-width: 680px; height: auto; }
.timeline text { font-family: var(--sans); fill: var(--ink-2); font-size: 12px; }
.timeline .role { font-size: 10.5px; font-weight: 600; letter-spacing: .06em; fill: var(--muted); }
.timeline .who { font-family: var(--mono); font-size: 12.5px; fill: var(--ink); }
.timeline .tick { fill: var(--muted); font-variant-numeric: tabular-nums; }
.timeline .inside { fill: var(--ink); font-size: 11.5px; font-weight: 600;
  paint-order: stroke; stroke: var(--surface); stroke-width: 3px; stroke-linejoin: round;
  pointer-events: none; }
.timeline .grid { stroke: var(--grid); stroke-width: 1; }
.timeline .base { stroke: var(--axis); stroke-width: 1; }
.timeline .marker { stroke: var(--ink); stroke-width: 1; stroke-dasharray: 3 3; opacity: .55; }
.timeline .badge { fill: var(--ink); }
.timeline .badge-n { fill: var(--surface); font-size: 11px; font-weight: 700; text-anchor: middle; }
.seg { outline: none; cursor: default; }
.seg .hit { fill: transparent; }
.seg:hover .mark, .seg:focus .mark { stroke: var(--ink); stroke-width: 1.5; }
.mark { stroke: var(--surface); stroke-width: 1; }
.mark.running, .swatch-running { fill: var(--running); }
.mark.ready, .swatch-ready { fill: url(#kv-ready); }
.mark.blocked, .swatch-blocked { fill: url(#kv-blocked); }
.mark.unknown { fill: none; stroke: var(--axis); stroke-dasharray: 4 3; }
.p-ready-bg { fill: var(--ready); } .p-ready-line { stroke: var(--ready-ink); stroke-width: 2; }
.p-blocked-bg { fill: var(--blocked); } .p-blocked-line { stroke: var(--blocked-ink); stroke-width: 2; }
.legend { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 18px;
  color: var(--ink-2); font-size: 13px; }
.legend span { display: inline-flex; align-items: center; gap: 6px; }
.legend svg { width: 22px; height: 12px; border-radius: 2px; }
.legend small { font-size: 12px; color: var(--muted); }
.markers { display: flex; flex-wrap: wrap; gap: 4px 18px; margin: 0; padding: 0;
  list-style: none; color: var(--ink-2); font-size: 13px; }
.markers b { display: inline-block; width: 18px; height: 18px; margin-right: 6px;
  border-radius: 50%; background: var(--ink); color: var(--surface);
  font-size: 11px; line-height: 18px; text-align: center; }
details { border-top: 1px solid var(--border); padding-top: 10px; }
summary { cursor: pointer; font-weight: 600; }
summary small { color: var(--muted); font-weight: 400; font-size: 13px; }
table { border-collapse: collapse; width: 100%; margin-top: 10px; font-size: 13px;
  font-variant-numeric: tabular-nums; }
th { text-align: left; font-weight: 600; color: var(--ink-2); white-space: nowrap; }
th, td { padding: 5px 12px 5px 0; border-bottom: 1px solid var(--grid); }
td.mono { white-space: nowrap; }
tr.worst td { font-weight: 600; }
.empty { color: var(--ink-2); }
footer { color: var(--muted); font-size: 12.5px; display: grid; gap: 4px; }
footer dl { display: flex; flex-wrap: wrap; gap: 2px 20px; margin: 0; }
footer dt { display: inline; font-weight: 600; }
footer dd { display: inline; margin: 0 0 0 5px; font-family: var(--mono); overflow-wrap: anywhere; }
#tip { position: fixed; z-index: 10; max-width: 320px; padding: 8px 10px;
  background: var(--ink); color: var(--surface); border-radius: 6px;
  font: 12.5px/1.5 var(--mono); white-space: pre-line; pointer-events: none; }
@media (max-width: 640px) {
  main { padding-inline: 16px; }
  .grid-2 { grid-template-columns: minmax(0, 1fr); }
  .banner .scope { margin-left: 0; text-align: left; }
}
"""

JS = """
(function () {
  var tip = document.getElementById('tip');
  function place(x, y) {
    var w = tip.offsetWidth, h = tip.offsetHeight;
    tip.style.left = Math.max(8, Math.min(x + 14, innerWidth - w - 8)) + 'px';
    tip.style.top = Math.max(8, (y + h + 20 > innerHeight) ? y - h - 12 : y + 16) + 'px';
  }
  function show(seg, x, y) {
    tip.textContent = seg.getAttribute('data-tip');
    tip.hidden = false;
    place(x, y);
  }
  function hide() { tip.hidden = true; }
  document.querySelectorAll('.seg').forEach(function (seg) {
    var title = seg.querySelector('title');
    if (title) title.remove();
    seg.addEventListener('pointermove', function (ev) { show(seg, ev.clientX, ev.clientY); });
    seg.addEventListener('pointerleave', hide);
    seg.addEventListener('focus', function () {
      var r = seg.getBoundingClientRect();
      show(seg, r.left, r.bottom);
    });
    seg.addEventListener('blur', hide);
  });
})();
"""

PATTERN_DEFS = """
<svg width="0" height="0" style="position:absolute" aria-hidden="true"><defs>
<pattern id="kv-ready" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
<rect class="p-ready-bg" width="8" height="8"/><line class="p-ready-line" x1="0" y1="0" x2="0" y2="8"/></pattern>
<pattern id="kv-blocked" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(135)">
<rect class="p-blocked-bg" width="8" height="8"/><line class="p-blocked-line" x1="0" y1="0" x2="0" y2="8"/></pattern>
</defs></svg>
"""


def esc(value):
    return html.escape(str(value), quote=True)


def plural(count, word):
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def thread_html(trace, thread):
    name = trace.thread_names.get(thread, "unnamed")
    return f'<code>{esc(name)}</code>'


def short_name(trace, thread):
    name = trace.thread_names.get(thread, f"{thread[0]}/{thread[1]}")
    return name if len(name) <= LABEL_CHARS else name[: LABEL_CHARS - 1] + "…"


class Scale:
    """Maps a block's cycle window onto chart x positions and milliseconds."""

    def __init__(self, trace, start, end):
        self.cps = trace.cycles_per_sec
        self.start = start
        self.span = max(end - start, 1)
        self.width = WIDTH - LEFT - RIGHT

    def x(self, cycles):
        return LEFT + (cycles - self.start) / self.span * self.width

    def ms(self, cycles):
        return (cycles - self.start) * 1000 / self.cps

    def duration_ms(self, cycles):
        return cycles * 1000 / self.cps


def ms_text(value):
    return f"{value:.2f} ms" if value >= 0.01 or value == 0 else f"{value:.4f} ms"


def nice_step(span_ms, target=5):
    raw = span_ms / target
    power = 10 ** math.floor(math.log10(raw))
    for multiple in (1, 2, 5, 10):
        if raw <= multiple * power:
            return multiple * power
    return 10 * power


def tooltip(segment, scale):
    duration = scale.duration_ms(segment.end_cycles - segment.start_cycles)
    lines = [
        f"{ms_text(duration)}  {segment.state} ({segment.category})",
        f"{ms_text(scale.ms(segment.start_cycles))} to {ms_text(scale.ms(segment.end_cycles))}",
    ]
    details = []
    if segment.priority is not None:
        details.append(f"priority {segment.priority}")
    if segment.cpu is not None:
        details.append(f"CPU {segment.cpu}")
    if segment.line is not None:
        details.append(f"line {segment.line}")
    if details:
        lines.append(", ".join(details))
    return "\n".join(lines)


def segment_svg(segment, scale, y):
    left = scale.x(segment.start_cycles)
    true_width = scale.x(segment.end_cycles) - left
    width = max(true_width - 2, MIN_MARK)
    x = left + 1 if true_width >= MIN_MARK + 2 else left
    radius = 4 if width >= 10 else 1
    hit_width = max(width, MIN_HIT)
    hit_x = x + (width - hit_width) / 2
    text = tooltip(segment, scale)
    label = ""
    duration = ms_text(scale.duration_ms(segment.end_cycles - segment.start_cycles))
    if width >= 150:
        label = f"{segment.state} · {duration}"
    elif width >= 70:
        label = segment.state
    inside = (
        f'<text class="inside" x="{x + 8:.1f}" y="{y + LANE_HEIGHT / 2 + 4:.1f}">{esc(label)}</text>'
        if label
        else ""
    )
    return (
        f'<g class="seg" tabindex="0" data-tip="{esc(text)}"><title>{esc(text)}</title>'
        f'<rect class="hit" x="{hit_x:.1f}" y="{y}" width="{hit_width:.1f}" height="{LANE_HEIGHT}"/>'
        f'<rect class="mark {esc(segment.category)}" x="{x:.1f}" y="{y}" width="{width:.1f}" '
        f'height="{LANE_HEIGHT}" rx="{radius}"/>{inside}</g>'
    )


def badge_positions(true_xs):
    """Keep numbered badges apart while their marker lines stay at the true x."""
    positions = []
    for x in true_xs:
        positions.append(max(x, positions[-1] + BADGE_SPACING) if positions else x)
    limit = WIDTH - 10
    for index in range(len(positions) - 1, -1, -1):
        positions[index] = min(positions[index], limit)
        limit = positions[index] - BADGE_SPACING
    return positions


def timeline_svg(trace, violation, lanes):
    scale = Scale(trace, violation.start_cycles, violation.end_cycles)
    bottom = TOP + len(lanes) * (LANE_HEIGHT + LANE_GAP) - LANE_GAP
    height = bottom + AXIS_HEIGHT
    parts = [
        f'<svg class="timeline" viewBox="0 0 {WIDTH} {height}" role="img" '
        f'aria-label="State timeline of the worst block; the table below has the same data">'
    ]

    span_ms = scale.ms(violation.end_cycles)
    step = nice_step(span_ms)
    tick = 0.0
    while tick <= span_ms + 1e-9:
        x = LEFT + tick / span_ms * scale.width
        parts.append(f'<line class="grid" x1="{x:.1f}" x2="{x:.1f}" y1="{TOP}" y2="{bottom}"/>')
        parts.append(
            f'<text class="tick" x="{x:.1f}" y="{bottom + 18}" text-anchor="middle">{tick:g}</text>'
        )
        tick += step
    parts.append(
        f'<text class="tick" x="{LEFT - 8}" y="{bottom + 18}" text-anchor="end">ms</text>'
        f'<line class="base" x1="{LEFT}" x2="{WIDTH - RIGHT}" y1="{bottom}" y2="{bottom}"/>'
    )

    for index, (role, thread, segments) in enumerate(lanes):
        y = TOP + index * (LANE_HEIGHT + LANE_GAP)
        parts.append(
            f'<text class="role" x="0" y="{y + 12}">{esc(role.upper())}</text>'
            f'<text class="who" x="0" y="{y + 28}">{esc(short_name(trace, thread))}'
            f"<title>{esc(trace.describe(thread))}</title></text>"
        )
        for segment in sorted(segments, key=lambda s: s.start_cycles - s.end_cycles):
            parts.append(segment_svg(segment, scale, y))

    marker_list = markers(violation)
    positions = badge_positions([scale.x(cycles) for _, _, cycles, _ in marker_list])
    for (number, _, cycles, _), badge_x in zip(marker_list, positions):
        x = scale.x(cycles)
        parts.append(
            f'<line class="marker" x1="{x:.1f}" x2="{x:.1f}" y1="{TOP - 8}" y2="{bottom}"/>'
            f'<line class="marker" x1="{badge_x:.1f}" x2="{x:.1f}" y1="20" y2="{TOP - 8}"/>'
            f'<circle class="badge" cx="{badge_x:.1f}" cy="11" r="9"/>'
            f'<text class="badge-n" x="{badge_x:.1f}" y="15">{number}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def markers(violation):
    return [
        (1, "block start", violation.start_cycles, violation.block_start_line),
        (2, "middle first ran", violation.middle_cycles, violation.middle_line),
        (3, "release", violation.release_cycles, violation.release_line),
        (4, "block end", violation.end_cycles, violation.block_end_line),
    ]


def legend_html():
    items = "".join(
        f'<span><svg viewBox="0 0 22 12" aria-hidden="true"><rect class="swatch-{key}" '
        f'width="22" height="12"/></svg>{key}</span>'
        for key in (RUNNING, READY, BLOCKED)
    )
    return (
        f'<div class="legend">{items}'
        "<small>Very short states are widened so they stay visible. "
        "Exact times are in the tooltips and the table.</small></div>"
    )


def markers_html(violation):
    items = "".join(
        f"<li><b>{number}</b>{esc(label)}, line {line}</li>"
        for number, label, _, line in markers(violation)
    )
    return f'<ol class="markers">{items}</ol>'


def segments_table(lanes, scale, trace):
    rows = []
    for role, thread, segments in lanes:
        for s in segments:
            rows.append(
                f"<tr><td>{esc(role)}</td><td><code>{esc(short_name(trace, thread))}</code></td>"
                f'<td class="mono">{esc(s.state)}</td><td>{esc(s.category)}</td>'
                f"<td>{ms_text(scale.ms(s.start_cycles))}</td>"
                f"<td>{ms_text(scale.ms(s.end_cycles))}</td>"
                f"<td>{ms_text(scale.duration_ms(s.end_cycles - s.start_cycles))}</td>"
                f'<td>{"" if s.priority is None else s.priority}</td>'
                f'<td>{"" if s.cpu is None else s.cpu}</td>'
                f'<td>{"" if s.line is None else s.line}</td></tr>'
            )
    return (
        '<div class="scroll"><table><thead><tr><th>Lane</th><th>Thread</th><th>State</th>'
        "<th>Category</th><th>Start</th><th>End</th><th>Duration</th><th>Priority</th>"
        f'<th>CPU</th><th>Line</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
    )


def occurrences_table(pattern, trace):
    worst = pattern.worst
    rows = []
    for number, v in enumerate(pattern.occurrences, 1):
        cls = ' class="worst"' if v is worst else ""
        rows.append(
            f"<tr{cls}><td>{number}{' (worst)' if v is worst else ''}</td>"
            f"<td>{trace.seconds(v.start_cycles):.6f}</td>"
            f"<td>{trace.seconds(v.end_cycles):.6f}</td>"
            f"<td>{v.duration_cycles / trace.cycles_per_sec:.3f}</td>"
            f'<td class="mono">{esc(v.kind)}</td><td class="mono">{esc(v.sync_p)}</td>'
            f"<td>{v.wait_call_line}</td><td>{v.block_start_line}</td><td>{v.middle_line}</td>"
            f"<td>{v.release_line}</td><td>{v.block_end_line}</td></tr>"
        )
    return (
        '<div class="scroll"><table><thead><tr><th>#</th><th>Start (s)</th><th>End (s)</th>'
        "<th>Block (s)</th><th>State</th><th>sync_p</th><th>Wait call</th><th>Block start</th>"
        "<th>Middle ran</th><th>Release</th><th>Block end</th></tr></thead>"
        f'<tbody>{"".join(rows)}</tbody></table></div>'
    )


def brief_ms(cycles, trace):
    value = cycles * 1000 / trace.cycles_per_sec
    return f"{value:.0f} ms" if value >= 1 else f"{value:.2f} ms"


def plain_summary(pattern, trace):
    amount = brief_ms(pattern.worst.duration_cycles, trace)
    if len(pattern.occurrences) > 1:
        amount = f"up to {amount}"
    return (
        f"The urgent thread {thread_html(trace, pattern.waiter)} waited {amount} for a lock "
        f"held by {thread_html(trace, pattern.holder)}, which couldn’t finish because "
        f"the less urgent {thread_html(trace, pattern.middle)} kept the CPU."
    )


def fix_lines(pattern):
    kinds = sorted({v.kind for v in pattern.occurrences}, reverse=True)
    return "".join(
        f'<p class="fix"><strong>Typical fix</strong>{esc(TYPICAL_FIXES[kind])}</p>'
        for kind in kinds
    )


def pattern_card(pattern, trace):
    worst = pattern.worst
    lanes = [
        (role, thread, thread_segments(trace, thread, worst.start_cycles, worst.end_cycles))
        for role, thread in (
            ("waiter", pattern.waiter),
            ("holder", pattern.holder),
            ("middle", pattern.middle),
        )
    ]
    scale = Scale(trace, worst.start_cycles, worst.end_cycles)
    worst_seconds = worst.duration_cycles / trace.cycles_per_sec
    count = len(pattern.occurrences)
    return f"""
<section class="card pattern">
  <h3>{thread_html(trace, pattern.waiter)} <em>(priority {worst.waiter_priority}) blocked by</em>
  {thread_html(trace, pattern.holder)} <em>(priority {worst.holder_priority}) while</em>
  {thread_html(trace, pattern.middle)} <em>(priority {worst.middle_priority}) ran</em></h3>
  <p class="plain">{plain_summary(pattern, trace)}</p>
  <div class="chips">
    <span class="chip"><b>{plural(count, "occurrence")}</b></span>
    <span class="chip">worst block <b>{worst_seconds:.3f} s</b></span>
    <span class="chip mono">{esc(worst.kind)}</span>
  </div>
  {fix_lines(pattern)}
  <div>
    <div class="eyebrow">Worst occurrence</div>
    <div class="scroll">{timeline_svg(trace, worst, lanes)}</div>
  </div>
  {legend_html()}
  {markers_html(worst)}
  <details><summary>Occurrences and evidence lines <small>{plural(count, "occurrence")}</small></summary>
  {occurrences_table(pattern, trace)}</details>
  <details><summary>Timeline as a table</summary>{segments_table(lanes, scale, trace)}</details>
</section>"""


def coverage_card(result):
    skipped = sum(result.not_evaluated.values())
    total = result.evaluated + skipped
    reasons = "".join(
        f"<li><b>{count}</b><span>{esc(reason)}</span></li>"
        for reason, count in sorted(result.not_evaluated.items())
    )
    reasons_html = f'<ul class="reasons">{reasons}</ul>' if reasons else ""
    return f"""
<section class="card">
  <div class="eyebrow">Coverage</div>
  <div class="stats">
    <div class="stat"><b>{result.evaluated}</b><span>evaluated</span></div>
    <div class="stat"><b>{skipped}</b><span>not evaluated</span></div>
    <div class="stat"><b>{total}</b><span>blocks in the trace</span></div>
  </div>
  <div class="meter" aria-hidden="true">
    <i class="done" style="flex:{result.evaluated} 1 0"></i><i style="flex:{skipped} 1 0"></i>
  </div>
  {reasons_html}
</section>"""


def notes_card():
    return f"""
<section class="card note">
  <div class="eyebrow">How to read this</div>
  <p><strong>Holders are {esc(HOLDER_NOTE)}.</strong> Each holder is the thread whose post
  or unlock on the same sync object released the waiter.</p>
  <p style="margin-top:8px">A block is a violation when a thread with priority strictly
  between the waiter and the holder ran on the holder's last CPU while the holder was in
  THREADY.</p>
</section>"""


def footer_html(path, trace):
    wanted = [
        ("Captured", "TRACE_DATE"),
        ("QNX", "TRACE_SYS_RELEASE"),
        ("CPUs", "TRACE_CPU_NUM"),
        ("Capture command", "TRACE_TRACELOGGER_ARGS"),
    ]
    items = "".join(
        f"<div><dt>{esc(label)}</dt><dd>{esc(trace.header[key])}</dd></div>"
        for label, key in wanted
        if key in trace.header
    )
    return f"""
<footer>
  <dl>{items}</dl>
  <div>Times come from the trace clock. Under QEMU, the structure matters more than the
  absolute values.</div>
</footer>"""


def render_html(path, trace, result):
    patterns = group_patterns(result.violations)
    total = len(result.violations)
    skipped = sum(result.not_evaluated.values())
    blocks = result.evaluated + skipped
    failed = bool(total)

    if failed:
        worst = patterns[0].worst.duration_cycles / trace.cycles_per_sec
        summary = (
            f"{plural(total, 'violation')} in {plural(len(patterns), 'pattern')}, "
            f"worst block {worst:.3f} s"
        )
        body = "".join(pattern_card(p, trace) for p in patterns)
    else:
        summary = "No priority inversion in the evaluated blocks"
        body = (
            '<section class="card empty">No violations in the blocks this rule could '
            "evaluate. See coverage above for the blocks it could not.</section>"
        )

    verdict = "FAIL" if failed else "PASS"
    status = "fail" if failed else "pass"
    icon = "✕" if failed else "✓"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="{CSP}">
<title>kevlint {verdict}: {esc(path)}</title>
<style>{CSS}</style>
</head>
<body>
{PATTERN_DEFS}
<main>
  <div class="brand"><b>kevlint</b><span>priority-inversion</span>
  <span class="path">{esc(path)}</span></div>
  <header class="banner {status}">
    <div class="icon" aria-hidden="true">{icon}</div>
    <div><div class="verdict">{verdict}</div><div class="summary">{esc(summary)}</div></div>
    <div class="scope"><b>{result.evaluated} of {blocks}</b> blocks evaluated<br>
    <b>{skipped}</b> not evaluated</div>
  </header>
  <div class="grid-2">{coverage_card(result)}{notes_card()}</div>
  {body}
  {footer_html(path, trace)}
</main>
<div id="tip" role="tooltip" hidden></div>
<script>{JS}</script>
</body>
</html>
"""
