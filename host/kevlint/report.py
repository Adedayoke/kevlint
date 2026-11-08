"""Render a check result as one self-contained HTML file.

Everything is inline and the page's content security policy forbids network
access. Timelines are SVG built here, so they display without JavaScript; the
script only adds richer tooltips over the SVG <title> fallback.
"""
import html
import math

from .config import INVERSION, MAX_BLOCKED
from .explain import CHECK_NOTE, WHERE_LABEL, line_list, pattern_key
from .inversion import BELOW_MINIMUM, HOLDER_NOTE, TYPICAL_FIXES, boost_text
from .patterns import group_patterns
from .rules import (
    ERROR,
    FAIL,
    NOT_EVALUATED,
    PASS,
    line_text,
    overall_status,
    root_cause_headline,
    root_cause_text,
    root_causes,
)
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
# Every report draws block length on this scale, so two reports compare directly.
RULER_MS = 300
RULER_STEP_MS = 100

CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
    "base-uri 'none'; form-action 'none'"
)

# Only reports with explanations carry this, so other reports stay unchanged.
EXPLANATION_CSS = """
.ai { padding: 10px 12px; border: 1px dashed var(--axis); border-left: 3px solid var(--warning); border-radius: 6px; }
.ai-label { font-weight: 600; }
.ai-label small { font-weight: 400; color: var(--muted); margin-left: 6px; }
.ai-check, .ai-note { color: var(--ink-2); margin: 4px 0 0; }
.ai ul { margin: 6px 0 0; padding-left: 20px; }
.ai-where { margin: 8px 0 0; padding-top: 8px; border-top: 1px solid var(--border); }
.ai-where strong { display: block; }
.ai-lines { font-family: var(--mono); font-size: .9em; color: var(--muted); white-space: nowrap; }
"""

# Only reports made with --config carry this, so other reports stay unchanged.
RULES_CSS = """
.banner.warn { --status: var(--warning); }
.banner.warn .icon { color: #0b0b0b; }
a.rule-link { color: inherit; font-family: var(--mono); }
.pill { display: inline-block; padding: 1px 9px; border-radius: 999px; font-size: 12px;
  font-weight: 700; letter-spacing: .02em; white-space: nowrap; color: #fff; }
.pill.pass { background: var(--good); }
.pill.fail { background: var(--critical); }
.pill.warn { background: var(--warning); color: #0b0b0b; }
.rule-title { display: flex; flex-wrap: wrap; align-items: center; gap: 10px;
  font: 600 17px var(--mono); margin-top: 18px; padding-top: 14px;
  border-top: 2px solid var(--axis); scroll-margin-top: 12px; }
.rule-card { display: grid; gap: 12px; }
.rule-card h3 { font-size: 13px; font-weight: 600; margin: 0; }
.rules-off { color: var(--muted); font-size: 13px; margin-top: 10px; }
.meter i.skipped, .meter-key i.skipped { background: var(--warning); }
.meter i.excluded, .meter-key i.excluded { background: var(--grid); }
.meter-key { display: flex; flex-wrap: wrap; gap: 4px 16px; margin: -6px 0 12px;
  color: var(--ink-2); font-size: 12.5px; }
.meter-key span { display: inline-flex; align-items: center; gap: 6px; }
.meter-key i { display: block; width: 12px; height: 8px; border-radius: 2px; }
.meter-key i.done { background: var(--ink-2); }
.stat.excluded b, .stat.excluded span, .reasons li.excluded, .reasons li.excluded b { color: var(--muted); }
.causes { margin: 0; padding: 0; list-style: none; display: grid; gap: 8px; }
.causes strong { display: block; font-family: var(--mono); font-size: 13px; overflow-wrap: anywhere; }
.cause-links { color: var(--ink-2); font-size: 13px; }
.rule-card .warn { margin-top: 0; }
"""

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
.priorities { margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--grid);
  color: var(--ink-2); font-variant-numeric: tabular-nums; }
.priorities b { color: var(--ink); }
.warn { display: flex; gap: 10px; align-items: flex-start; margin-top: 12px;
  padding: 10px 12px; background: var(--code); border-left: 4px solid var(--warning);
  border-radius: 6px; }
.warn .mark-warn { flex: none; width: 20px; height: 20px; border-radius: 50%;
  background: var(--warning); color: #0b0b0b; font: 700 13px/20px var(--sans);
  text-align: center; }
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
.chip.ok { border-color: var(--good); color: var(--ink); }
.chip.ok::before { content: "\\2713"; margin-right: 6px; color: var(--good); font-weight: 700; }
.section-title { font-size: 12px; font-weight: 600; letter-spacing: .06em;
  text-transform: uppercase; color: var(--muted); margin: 6px 0 -6px; }
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


def segment_svg(segment, scale, y, show_priority=False):
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
    if show_priority and width >= 70 and segment.priority is not None:
        label = f"priority {segment.priority}"
        if width >= 240:
            label = f"{segment.state} · priority {segment.priority} · {duration}"
    elif width >= 150:
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


def timeline_svg(trace, violation, lanes, marker_list, priority_roles=()):
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
            parts.append(segment_svg(segment, scale, y, role in priority_roles))

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


def markers(item, middle_label="middle first ran"):
    rows = [("block start", item.start_cycles, item.block_start_line)]
    if item.middle_cycles is not None:
        rows.append((middle_label, item.middle_cycles, item.middle_line))
    rows.append(("release", item.release_cycles, item.release_line))
    rows.append(("block end", item.end_cycles, item.block_end_line))
    return [(number, *row) for number, row in enumerate(rows, 1)]


def duration_ruler(cycles, trace):
    """Draw block length on a scale shared by every report."""
    value = cycles * 1000 / trace.cycles_per_sec
    top = max(RULER_MS, math.ceil(value / RULER_STEP_MS) * RULER_STEP_MS)
    plot = WIDTH - LEFT - RIGHT
    width = max(value / top * plot, MIN_MARK)
    ticks = "".join(
        f'<line class="grid" x1="{LEFT + mark / top * plot:.1f}" '
        f'x2="{LEFT + mark / top * plot:.1f}" y1="0" y2="24"/>'
        f'<text class="tick" x="{LEFT + mark / top * plot:.1f}" y="40" '
        f'text-anchor="middle">{mark}</text>'
        for mark in range(0, top + 1, RULER_STEP_MS)
    )
    text = f"{ms_text(value)} blocked, on a fixed 0 to {top} ms scale"
    return (
        f'<svg class="timeline ruler" viewBox="0 0 {WIDTH} 44" role="img" '
        f'aria-label="{esc(text)}">{ticks}'
        '<text class="role" x="0" y="11">BLOCK LENGTH</text>'
        f'<text class="tick" x="0" y="26">shared {top} ms scale</text>'
        f'<text class="tick" x="{LEFT - 8}" y="40" text-anchor="end">ms</text>'
        f'<g class="seg" tabindex="0" data-tip="{esc(text)}"><title>{esc(text)}</title>'
        f'<rect class="hit" x="{LEFT}" y="2" width="{max(width, MIN_HIT):.1f}" height="20"/>'
        f'<rect class="mark blocked" x="{LEFT}" y="2" width="{width:.1f}" height="20" rx="4"/></g>'
        f'<text class="inside" x="{LEFT + width + 8:.1f}" y="17">{esc(ms_text(value))}</text>'
        "</svg>"
    )


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


def markers_html(marker_list):
    items = "".join(
        f"<li><b>{number}</b>{esc(label)}, line {line}</li>"
        for number, label, _, line in marker_list
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


def occurrences_table(pattern, trace, middle_heading="Middle ran"):
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
            f"<td>{v.wait_call_line}</td><td>{v.block_start_line}</td>"
            f'<td>{"" if v.middle_line is None else v.middle_line}</td>'
            f"<td>{v.release_line}</td><td>{v.block_end_line}</td></tr>"
        )
    return (
        '<div class="scroll"><table><thead><tr><th>#</th><th>Start (s)</th><th>End (s)</th>'
        "<th>Block (s)</th><th>State</th><th>sync_p</th><th>Wait call</th><th>Block start</th>"
        f"<th>{esc(middle_heading)}</th><th>Release</th><th>Block end</th></tr></thead>"
        f'<tbody>{"".join(rows)}</tbody></table></div>'
    )


def brief_ms(cycles, trace):
    value = cycles * 1000 / trace.cycles_per_sec
    return f"{value:.0f} ms" if value >= 1 else f"{value:.2f} ms"


def plain_summary(pattern, trace, thread_text=thread_html):
    amount = brief_ms(pattern.worst.duration_cycles, trace)
    if len(pattern.occurrences) > 1:
        amount = f"up to {amount}"
    return (
        f"The urgent thread {thread_text(trace, pattern.waiter)} waited {amount} for a lock "
        f"held by {thread_text(trace, pattern.holder)}, which couldn’t finish because "
        f"the less urgent {thread_text(trace, pattern.middle)} kept the CPU."
    )


def fix_lines(pattern):
    kinds = sorted({v.kind for v in pattern.occurrences}, reverse=True)
    return "".join(
        f'<p class="fix"><strong>Typical fix</strong>{esc(TYPICAL_FIXES[kind])}</p>'
        for kind in kinds
    )


def explanation_html(explanation):
    """The labeled AI block for one pattern; model text is always escaped."""
    if explanation is None:
        return ""
    if explanation.note is not None:
        content = f'<p class="ai-note">{esc(explanation.note)}</p>'
    else:
        items = "".join(
            f'<li>{esc(claim.text)} <span class="ai-lines">{esc(line_list(claim.lines))}</span></li>'
            for claim in explanation.claims
        )
        content = f'<p class="ai-check">{esc(CHECK_NOTE)}</p><ul>{items}</ul>'
        if explanation.where is not None:
            where = explanation.where
            content += (
                f'<p class="ai-where"><strong>{esc(WHERE_LABEL)}</strong>{esc(where.text)} '
                f'<span class="ai-lines">{esc(line_list(where.lines))}</span></p>'
            )
    return (
        '<div class="ai"><div class="ai-label">AI-generated explanation'
        f"<small>worst of {plural(explanation.occurrences, 'occurrence')}</small></div>"
        f"{content}</div>"
    )


def pattern_card(pattern, trace, explanation=None):
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
    marker_list = markers(worst)
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
  {fix_lines(pattern)}{explanation_html(explanation)}
  <div class="scroll">{duration_ruler(worst.duration_cycles, trace)}</div>
  <div>
    <div class="eyebrow">Worst occurrence</div>
    <div class="scroll">{timeline_svg(trace, worst, lanes, marker_list)}</div>
  </div>
  {legend_html()}
  {markers_html(marker_list)}
  <details><summary>Occurrences and evidence lines <small>{plural(count, "occurrence")}</small></summary>
  {occurrences_table(pattern, trace)}</details>
  <details><summary>Timeline as a table</summary>{segments_table(lanes, scale, trace)}</details>
</section>"""


def resolved_card(pattern, trace):
    worst = pattern.worst
    roles = [("waiter", pattern.waiter), ("holder", pattern.holder)]
    if pattern.middle is not None:
        roles.append(("middle", pattern.middle))
    lanes = [
        (role, thread, thread_segments(trace, thread, worst.start_cycles, worst.end_cycles))
        for role, thread in roles
    ]
    scale = Scale(trace, worst.start_cycles, worst.end_cycles)
    seconds = worst.duration_cycles / trace.cycles_per_sec
    count = len(pattern.occurrences)
    marker_list = markers(worst, "middle became ready")
    waiter = thread_html(trace, pattern.waiter)
    holder = thread_html(trace, pattern.holder)
    boosted = worst.holder_peak_priority > worst.holder_base_priority
    inherited = boosted and worst.kind == "THMUTEX"
    holder_priority = (
        f"{worst.holder_base_priority} → {worst.holder_peak_priority}"
        if boosted
        else worst.holder_base_priority
    )
    amount = brief_ms(worst.duration_cycles, trace)
    if count > 1:
        amount = f"up to {amount}"

    title_end = "<em>held the lock</em>"
    middle_sentence = ""
    middle_chip = ""
    if pattern.middle is not None:
        middle = thread_html(trace, pattern.middle)
        outcome = "ran" if worst.middle_ran else "stayed READY"
        title_end = (
            f"<em>while</em> {middle} <em>(priority {worst.middle_priority}) {outcome}</em>"
        )
        middle_sentence = (
            f" The less urgent {middle} also ran during this block."
            if worst.middle_ran
            else f" The less urgent {middle} stayed READY and never took the CPU."
        )
        if not worst.middle_ran:
            middle_chip = f'<span class="chip ok">{middle} stayed READY</span>'

    holder_outcome = (
        "which ran at the waiter’s priority until it released the lock."
        if inherited
        else "which released it."
    )
    why = (
        '<p class="fix"><strong>Why it worked</strong>QNX mutexes use priority inheritance, '
        "so the holder could not be held up by lower-priority work.</p>"
        if inherited
        else ""
    )
    return f"""
<section class="card pattern resolved">
  <h3>{waiter} <em>(priority {worst.waiter_priority}) waited on</em>
  {holder} <em>(priority {holder_priority})</em> {title_end}</h3>
  <p class="plain">The urgent thread {waiter} waited {amount} for a lock held by {holder},
  {holder_outcome}{middle_sentence}</p>
  <div class="chips">
    <span class="chip"><b>{plural(count, "occurrence")}</b></span>
    <span class="chip">worst block <b>{seconds:.3f} s</b></span>
    <span class="chip mono">{esc(worst.kind)}</span>
    <span class="chip{" ok" if boosted else ""}">{holder} {esc(boost_text(worst))}</span>
    {middle_chip}
  </div>
  {why}
  <div class="scroll">{duration_ruler(worst.duration_cycles, trace)}</div>
  <div>
    <div class="eyebrow">Worst occurrence</div>
    <div class="scroll">{timeline_svg(trace, worst, lanes, marker_list, ("holder",))}</div>
  </div>
  {legend_html()}
  {markers_html(marker_list)}
  <details><summary>Occurrences and evidence lines <small>{plural(count, "occurrence")}</small></summary>
  {occurrences_table(pattern, trace, "Middle ready")}</details>
  <details><summary>Timeline as a table</summary>{segments_table(lanes, scale, trace)}</details>
</section>"""


def priority_text(priority):
    return "none" if priority is None else priority


def coverage_warning(result):
    if not result.unevaluated_outranks_evaluated:
        return ""
    unchecked = result.not_evaluated_max_priority
    checked = result.evaluated_max_priority
    compared = (
        "and no waiter could be evaluated"
        if checked is None
        else f"above the highest evaluated waiter ({checked})"
    )
    return (
        '<div class="warn" role="alert"><span class="mark-warn" aria-hidden="true">!</span>'
        "<div><strong>Warning: higher-priority blocks were not evaluated.</strong> "
        f"A not-evaluated block has priority {unchecked}, {compared}. "
        "This result does not cover those threads.</div></div>"
    )


def coverage_card(result):
    skipped = sum(result.not_evaluated.values())
    excluded = result.excluded
    total = result.evaluated + skipped + excluded
    reasons = "".join(
        f"<li><b>{count}</b><span>{esc(reason)}</span></li>"
        for reason, count in sorted(result.not_evaluated.items())
    )
    if excluded:
        reasons += f'<li class="excluded"><b>{excluded}</b><span>{esc(BELOW_MINIMUM)}</span></li>'
    reasons_html = f'<ul class="reasons">{reasons}</ul>' if reasons else ""
    excluded_stat = (
        f'<div class="stat excluded"><b>{excluded}</b><span>excluded by config</span></div>'
        if excluded
        else ""
    )
    skipped_class = ' class="skipped"' if excluded else ""
    excluded_bar = f'<i class="excluded" style="flex:{excluded} 1 0"></i>' if excluded else ""
    meter_key = (
        '<div class="meter-key"><span><i class="done"></i>evaluated</span>'
        '<span><i class="skipped"></i>not evaluated</span>'
        '<span><i class="excluded"></i>excluded by config</span></div>'
        if excluded
        else ""
    )
    checked = result.evaluated_max_priority
    unchecked = result.not_evaluated_max_priority
    return f"""
<section class="card">
  <div class="eyebrow">Coverage</div>
  <div class="stats">
    <div class="stat"><b>{result.evaluated}</b><span>evaluated</span></div>
    <div class="stat"><b>{skipped}</b><span>not evaluated</span></div>{excluded_stat}
    <div class="stat"><b>{total}</b><span>blocks in the trace</span></div>
  </div>
  <div class="meter" aria-hidden="true">
    <i class="done" style="flex:{result.evaluated} 1 0"></i><i{skipped_class} style="flex:{skipped} 1 0"></i>{excluded_bar}
  </div>{meter_key}
  {reasons_html}
  <div class="priorities">Highest waiter priority:
  <b>{priority_text(checked)}</b> evaluated, <b>{priority_text(unchecked)}</b> not evaluated</div>
  {coverage_warning(result)}
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


VISIBLE_ROWS = 3
STATUS_CLASS = {PASS: "pass", FAIL: "fail", ERROR: "fail", NOT_EVALUATED: "warn"}
STATUS_ICON = {PASS: "✓", FAIL: "✕", ERROR: "✕", NOT_EVALUATED: "!"}
VISIBILITY_NOTE = (
    "A block already in progress when the trace started has no visible start, so it "
    "cannot be measured."
)


def pill(status):
    return f'<span class="pill {STATUS_CLASS[status]}">{esc(status)}</span>'


def ms_cell(value):
    return "none" if value is None else f"{value:.3f}"


def rule_anchor(name):
    return f"rule-{name}"


def alert(title, text):
    return (
        '<div class="warn" role="alert"><span class="mark-warn" aria-hidden="true">!</span>'
        f"<div><strong>{esc(title)}</strong> {esc(text)}</div></div>"
    )


def rules_overview(rules, config):
    rows = "".join(
        f'<tr><td><a class="rule-link" href="#{esc(rule_anchor(rule.name))}">{esc(rule.name)}</a></td>'
        f"<td>{pill(rule.status)}</td><td>{len(rule.findings)}</td>"
        f"<td>{ms_cell(rule.worst_ms)}</td><td>{esc(rule.coverage)}</td></tr>"
        for rule in rules
    )
    off = (
        f'<p class="rules-off">Not enabled: {esc(", ".join(config.disabled))}</p>'
        if config.disabled
        else ""
    )
    return f"""
<section class="card">
  <div class="eyebrow">Results by rule</div>
  <div class="scroll"><table><thead><tr><th>Rule</th><th>Result</th><th>Findings</th>
  <th>Worst block (ms)</th><th>Coverage</th></tr></thead><tbody>{rows}</tbody></table></div>
  {off}
</section>"""


def root_causes_card(rules, trace):
    """List the causes when different rules' findings share one, else nothing."""
    if root_cause_headline(trace, rules) is None:
        return ""
    items = []
    for cause in root_causes(rules):
        links = ", ".join(
            f'<a class="rule-link" href="#{esc(rule_anchor(name))}">{count} {esc(name)}</a>'
            for name, count in cause.counts.items()
        )
        items.append(
            f"<li><strong>{esc(root_cause_text(trace, cause))}</strong>"
            f'<span class="cause-links">{links}</span></li>'
        )
    return f"""
<section class="card">
  <div class="eyebrow">Root causes</div>
  <ul class="causes">{"".join(items)}</ul>
  <p class="rules-off">Findings are grouped by thread and sync object.</p>
</section>"""


def rule_coverage(rule):
    reasons = "".join(
        f"<li><b>{count}</b><span>{esc(reason)}</span></li>"
        for reason, count in sorted(rule.not_evaluated.items())
    )
    reasons_html = f'<ul class="reasons">{reasons}</ul>' if reasons else ""
    return f"<div><h3>Coverage: {esc(rule.coverage)}</h3>{reasons_html}</div>"


def rule_alerts(rule):
    return "".join(alert("Error:", text) for text in rule.errors) + "".join(
        alert("Warning:", text) for text in rule.warnings
    )


def rule_heading(rule):
    return (
        f'<h2 class="rule-title" id="{esc(rule_anchor(rule.name))}">{esc(rule.name)} '
        f"{pill(rule.status)}</h2>"
    )


def finding_duration(finding, trace):
    millis = finding.duration_cycles * 1000 / trace.cycles_per_sec
    if finding.end_cycles is None:
        return f"at least {millis:.3f} (still open at trace end)"
    return f"{millis:.3f}"


def holder_cell(finding, trace):
    return "not visible" if finding.holder is None else thread_html(trace, finding.holder)


def budgets_table(rule):
    rows = "".join(
        f'<tr><td class="mono">{esc(row.budget.thread)}</td><td>{row.budget.max_ms:g}</td>'
        f'<td class="mono">{esc(", ".join(row.budget.states))}</td>'
        f"<td>{pill(row.status)}</td><td>{row.evaluated}</td><td>{row.breaches}</td>"
        f"<td>{ms_cell(row.worst_ms)}</td></tr>"
        for row in rule.budgets
    )
    return (
        '<div class="scroll"><table><thead><tr><th>Thread</th><th>Budget (ms)</th>'
        "<th>States counted</th><th>Result</th><th>Evaluated</th><th>Breaches</th>"
        f"<th>Worst (ms)</th></tr></thead><tbody>{rows}</tbody></table></div>"
    )


def worst_rows(head, rows, durations, noun):
    """Show the worst rows, with every row in trace order behind a toggle."""
    wrap = lambda body: (
        f'<div class="scroll"><table><thead><tr>{head}</tr></thead>'
        f"<tbody>{body}</tbody></table></div>"
    )
    order = sorted(range(len(rows)), key=lambda i: durations[i], reverse=True)[:VISIBLE_ROWS]
    shown = wrap("".join(rows[i] for i in order))
    if len(rows) <= VISIBLE_ROWS:
        return shown
    return (
        f"{shown}<details><summary>Show all {len(rows)} {noun}</summary>"
        f'{wrap("".join(rows))}</details>'
    )


def rows_heading(label, count):
    shown = f", worst {VISIBLE_ROWS} shown" if count > VISIBLE_ROWS else ""
    return f"<h3>{label} ({count}){shown}</h3>"


def breaches_table(rule, trace):
    worst = max(rule.findings, key=lambda f: f.duration_cycles)
    rows = []
    for number, finding in enumerate(rule.findings, 1):
        millis = finding.duration_cycles * 1000 / trace.cycles_per_sec
        cls = ' class="worst"' if finding is worst else ""
        rows.append(
            f"<tr{cls}><td>{number}</td><td>{thread_html(trace, finding.thread)}</td>"
            f"<td>{priority_text(finding.priority)}</td>"
            f'<td class="mono">{esc(finding.state)}</td>'
            f'<td class="mono">{esc(finding.sync_p or "not visible")}</td>'
            f"<td>{finding_duration(finding, trace)}</td><td>{finding.budget_ms:g}</td>"
            f"<td>{millis - finding.budget_ms:.3f}</td>"
            f"<td>{trace.seconds(finding.start_cycles):.6f}</td>"
            f"<td>{holder_cell(finding, trace)}</td>"
            f"<td>{esc(line_text(finding.lines))}</td></tr>"
        )
    head = (
        "<th>#</th><th>Thread</th><th>Priority</th><th>State</th><th>sync_p</th>"
        "<th>Blocked (ms)</th><th>Budget (ms)</th><th>Over by (ms)</th><th>Start (s)</th>"
        "<th>Holder (inferred)</th><th>Evidence lines</th>"
    )
    return worst_rows(head, rows, [f.duration_cycles for f in rule.findings], "breaches")


def semaphore_table(rule, trace):
    worst = max(rule.findings, key=lambda f: f.duration_cycles)
    rows = []
    for number, finding in enumerate(rule.findings, 1):
        cls = ' class="worst"' if finding is worst else ""
        rows.append(
            f"<tr{cls}><td>{number}</td><td>{thread_html(trace, finding.thread)}</td>"
            f"<td>{priority_text(finding.priority)}</td>"
            f'<td class="mono">{esc(finding.sync_p or "not visible")}</td>'
            f"<td>{finding_duration(finding, trace)}</td>"
            f"<td>{trace.seconds(finding.start_cycles):.6f}</td>"
            f"<td>{holder_cell(finding, trace)}</td>"
            f"<td>{esc(line_text(finding.lines))}</td></tr>"
        )
    head = (
        "<th>#</th><th>Thread</th><th>Priority</th><th>sync_p</th><th>Blocked (ms)</th>"
        "<th>Start (s)</th><th>Holder (inferred)</th><th>Evidence lines</th>"
    )
    return worst_rows(head, rows, [f.duration_cycles for f in rule.findings], "findings")


def max_blocked_section(rule, trace):
    breaches = (
        f'<div>{rows_heading("Breaches", len(rule.findings))}{breaches_table(rule, trace)}</div>'
        if rule.findings
        else '<p class="empty">No block exceeded its budget.</p>'
    )
    return f"""
{rule_heading(rule)}
<section class="card rule-card">
  <p class="plain">Each named thread must not stay in the listed states longer than its
  budget. Time in THREADY is not counted. {esc(VISIBILITY_NOTE)}</p>
  {rule_alerts(rule)}
  {budgets_table(rule)}
  {breaches}
  {rule_coverage(rule)}
</section>"""


def semaphore_section(rule, trace, minimum):
    findings = (
        f'<div>{rows_heading("Findings", len(rule.findings))}{semaphore_table(rule, trace)}</div>'
        f'<p class="fix"><strong>Typical fix</strong>{esc(TYPICAL_FIXES["THSEM"])}</p>'
        if rule.findings
        else f'<p class="empty">No thread at priority {minimum} or above blocked on a semaphore.</p>'
    )
    return f"""
{rule_heading(rule)}
<section class="card rule-card">
  <p class="plain">Flags each entry to THSEM at priority {minimum} or above, because
  semaphores have no priority inheritance. {esc(VISIBILITY_NOTE)}</p>
  {rule_alerts(rule)}
  {findings}
  {rule_coverage(rule)}
</section>"""


def inversion_section(rule, trace, explanations):
    result = rule.inversion
    _, body = inversion_summary_and_body(result, trace, explanations)
    return f"""
{rule_heading(rule)}
<div class="grid-2">{coverage_card(result)}{notes_card()}</div>
{body}"""


def rule_section(rule, trace, config, explanations):
    if rule.name == INVERSION:
        return inversion_section(rule, trace, explanations)
    if rule.name == MAX_BLOCKED:
        return max_blocked_section(rule, trace)
    return semaphore_section(rule, trace, config.semaphore_min_priority)


def rules_summary(rules, trace):
    headline = root_cause_headline(trace, rules)
    if headline is not None:
        return headline
    failed = sum(rule.status in (FAIL, ERROR) for rule in rules)
    skipped = sum(rule.status == NOT_EVALUATED for rule in rules)
    findings = sum(len(rule.findings) for rule in rules)
    if failed:
        return f"{failed} of {plural(len(rules), 'rule')} failed, {plural(findings, 'finding')}"
    if skipped:
        return f"{skipped} of {plural(len(rules), 'rule')} not evaluated"
    return "All rules passed in the blocks they evaluated"


def render_rules_html(path, trace, rules, config, explanations):
    verdict = overall_status(rules)
    scope = (
        f"<b>{plural(len(rules), 'rule')}</b> run<br>\n    <b>{len(config.disabled)}</b> not enabled"
    )
    body = "".join(rule_section(rule, trace, config, explanations) for rule in rules)
    return html_page(
        path,
        trace,
        verdict,
        STATUS_CLASS[verdict],
        STATUS_ICON[verdict],
        "rules",
        rules_summary(rules, trace),
        scope,
        rules_overview(rules, config) + root_causes_card(rules, trace),
        body,
        CSS + (EXPLANATION_CSS if explanations else "") + RULES_CSS,
    )


def inversion_summary_and_body(result, trace, explanations):
    """Return the banner summary and the cards for the inversion rule."""
    patterns = group_patterns(result.violations)
    total = len(result.violations)
    if total:
        worst = patterns[0].worst.duration_cycles / trace.cycles_per_sec
        summary = (
            f"{plural(total, 'violation')} in {plural(len(patterns), 'pattern')}, "
            f"worst block {worst:.3f} s"
        )
        body = '<h2 class="section-title">Priority inversions</h2>' + "".join(
            pattern_card(p, trace, (explanations or {}).get(pattern_key(p))) for p in patterns
        )
    else:
        summary = "No priority inversion in the evaluated blocks"
        body = (
            ""
            if result.resolved
            else '<section class="card empty">No violations in the blocks this rule could '
            "evaluate. See coverage above for the blocks it could not.</section>"
        )

    if result.resolved:
        worst_resolved = group_patterns(result.resolved)[0]
        body += (
            '<h2 class="section-title">High-priority waits that resolved correctly</h2>'
            + resolved_card(worst_resolved, trace)
        )

    return summary, body


def render_html(path, trace, result, explanations=None, rules=None, config=None):
    if rules is not None:
        return render_rules_html(path, trace, rules, config, explanations)
    summary, body = inversion_summary_and_body(result, trace, explanations)
    skipped = sum(result.not_evaluated.values())
    blocks = result.evaluated + skipped
    failed = bool(result.violations)
    scope = (
        f"<b>{result.evaluated} of {blocks}</b> blocks evaluated<br>\n"
        f"    <b>{skipped}</b> not evaluated"
    )
    return html_page(
        path,
        trace,
        "FAIL" if failed else "PASS",
        "fail" if failed else "pass",
        "✕" if failed else "✓",
        "priority-inversion",
        summary,
        scope,
        f'<div class="grid-2">{coverage_card(result)}{notes_card()}</div>',
        body,
        CSS + (EXPLANATION_CSS if explanations else ""),
    )


def html_page(path, trace, verdict, status, icon, brand, summary, scope, top, body, styles):
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="{CSP}">
<title>kevlint {verdict}: {esc(path)}</title>
<style>{styles}</style>
</head>
<body>
{PATTERN_DEFS}
<main>
  <div class="brand"><b>kevlint</b><span>{esc(brand)}</span>
  <span class="path">{esc(path)}</span></div>
  <header class="banner {status}">
    <div class="icon" aria-hidden="true">{icon}</div>
    <div><div class="verdict">{verdict}</div><div class="summary">{esc(summary)}</div></div>
    <div class="scope">{scope}</div>
  </header>
  {top}
  {body}
  {footer_html(path, trace)}
</main>
<div id="tip" role="tooltip" hidden></div>
<script>{JS}</script>
</body>
</html>
"""
