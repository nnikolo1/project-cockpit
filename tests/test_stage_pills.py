"""Colour-coded stage pills and filter chips in the Task Queue (PC-127), on synthetic tasks: every stage has its colour tokens in
both themes, a readable contrast, a tooltip and one shared helper, so a new stage without a colour fails here."""
import json
import pathlib
import re
import shutil
import subprocess

import pytest

PAGE = (pathlib.Path(__file__).parent.parent / "web" / "index.html").read_text()
node = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
STAGES = json.loads(re.search(r"const STAGES=(\[[^\]]*\])", PAGE).group(1).replace("'", '"'))
MARKERS = STAGES + ["blocked"]
BLOCK = PAGE[PAGE.index("/* stage-pills:start"):PAGE.index("/* stage-pills:end */")]
TIPS = dict(re.findall(r"(\w+):'([^']*)'", BLOCK[BLOCK.index("STAGE_TIP"):BLOCK.index("};")]))
LABEL = dict(re.findall(r"(\w+):'([^']*)'", re.search(r"const STAGE_LABEL=\{[^}]*\}", PAGE).group(0)))


def themes():
    """The three token blocks: light :root, dark by the system setting, dark by choice."""
    css = PAGE[:PAGE.index("</style>")]
    light = css[css.index(":root{"):css.index("}", css.index(":root{"))]
    dark_sys = css[css.index(":root:not([data-theme=\"light\"]){"):css.index("}}", css.index("prefers-color-scheme:dark"))]
    dark = css[css.index(':root[data-theme="dark"]{'):css.index("}", css.index(':root[data-theme="dark"]{'))]
    return {"light": light, "dark (system)": dark_sys, "dark": dark}


def token(block, name):
    m = re.search(r"--%s:(#[0-9a-fA-F]{6});" % re.escape(name), block)
    assert m, "token --%s missing" % name
    return m.group(1)


def contrast(a, b):
    def lum(h):
        c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_every_stage_has_tokens_in_every_theme_with_readable_contrast():
    for name, block in themes().items():
        surface = token(block, "surface")
        for s in MARKERS:
            ink, tint = token(block, "s-" + s), token(block, "s-%s-bg" % s)
            assert contrast(ink, tint) >= 4.5, (name, s, "word on its tint")
            assert contrast(ink, surface) >= 4.5, (name, s, "against the row background")
            assert contrast(surface, ink) >= 4.5, (name, s, "selected filter chip")
        hues = [token(block, "s-" + s).lower() for s in STAGES]
        assert len(set(hues)) == len(hues), (name, "one distinct hue per stage")


def test_every_stage_has_its_class_and_a_one_line_tooltip():
    assert set(TIPS) == set(MARKERS), "a stage without a tooltip, or a tooltip without a stage"
    for s in MARKERS:
        assert ".s-%s{--c:var(--s-%s);--cb:var(--s-%s-bg)}" % (s, s, s) in PAGE, s
        assert TIPS[s].startswith(LABEL.get(s, s) + ": ") and "\n" not in TIPS[s] and len(TIPS[s]) > len(s) + 10, s
    assert TIPS["unmerged"] == "unmerged: the Worker finished, its result waits for review and merge"
    assert ".stg.s-cancelled{text-decoration:line-through" in PAGE and ".stg.s-blocked{border-color:var(--c)}" in PAGE


def test_one_shared_helper_shows_the_stage_everywhere():
    outside = PAGE.replace(BLOCK, "")
    # the stage word is rendered only by the helper (the label table, the filter list and the stage bar aside)
    assert not re.search(r"STAGE_LABEL\[(t|k|x)\.stage\]", outside) and "STAGE_LABEL[x]" not in outside
    assert "<b>WIP</b>" not in PAGE
    assert outside.count("stageHtml(t,isBlocked(D,t))") == 3  # the table, the drawer header, the Timeline's upcoming deadlines
    assert "stagePill(k.stage)" in outside and "stagePill(s,cnt[s])" in outside and "fl.map(x=>stageChip(x,f===x))" in outside
    assert "stageLegend()" in outside and "background:var(--s-'+s+')" in outside  # the stage bar takes the same tokens
    assert "<details>" in BLOCK and "<details open" not in BLOCK  # the legend is collapsed by default


# --- rendering, on synthetic tasks ---
def run_node(code):
    esc = re.search(r"^function esc\(s\).*$", PAGE, re.M).group(0)
    head = re.search(r"^const STAGES=.*$", PAGE, re.M).group(0) + "\n" + re.search(r"^const STAGE_LABEL=.*$", PAGE, re.M).group(0)
    r = subprocess.run(["node", "-e", esc + "\n" + head + "\n" + BLOCK + "\n" + code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@node
def test_each_stage_renders_its_pill_word_class_and_tooltip():
    tasks = [dict(key="x-%d" % i, stage=s) for i, s in enumerate(STAGES)]
    out = run_node("console.log(JSON.stringify(%s.map(t=>[stageHtml(t,false),stageHtml(t,true)])))" % json.dumps(tasks))
    for t, (plain, blocked) in zip(tasks, out):
        s = t["stage"]
        assert plain == '<span class="stg s-%s" title="%s">%s</span>' % (s, TIPS[s], LABEL[s])
        assert blocked.startswith(plain) and 'class="stg s-blocked"' in blocked and ">blocked</span>" in blocked


@node
def test_filter_chips_follow_the_stage_classes():
    fl = ["all"] + MARKERS
    out = run_node("console.log(JSON.stringify(%s.map(x=>[stageChip(x,false),stageChip(x,true)])))" % json.dumps(fl))
    for x, (off, on) in zip(fl, out):
        assert 'aria-pressed="false"' in off and 'aria-pressed="true"' in on
        if x == "all":
            assert off.startswith('<button class="f"') and "title=" not in off
        else:
            assert off.startswith('<button class="f sf s-%s"' % x) and ('title="%s"' % TIPS[x]) in off


@node
def test_legend_lists_every_stage_and_counts_show_in_pills():
    out = run_node("console.log(JSON.stringify([stageLegend(),stagePill('wip',3)]))")
    for s in MARKERS:
        assert 'class="stg s-%s"' % s in out[0]
    assert out[1].endswith(">3 WIP</span>")
