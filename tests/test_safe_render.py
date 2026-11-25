"""PC-114: a launch time of any type must not crash a tab, and one block that throws must not leave a whole tab undrawn."""
import json
import pathlib
import re
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
import logic as L  # noqa: E402

PAGE = (ROOT / "web" / "index.html").read_text()
node = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")


@pytest.mark.parametrize("launched, want", [
    (1795577286.197, "2026-11-25T"),         # epoch float, as the Dispatcher writes it
    (0, "1970-01-01T00:00+00:00"),           # epoch int
    ("2026-11-25T05:28:06+00:00", "2026-11-25T05:28:06+00:00"),  # a string passes through
    (None, None), ([1], None), (True, None), (float("nan"), None), (1e300, None),
])
def test_running_workers_carry_the_launch_time_as_a_string(launched, want):
    st = json.dumps(dict(workers={"w1": dict(task_id="PC-1", launched=launched, started=True)}))
    got = L.queue_local(st, None)["workers"][0]["launched"]
    assert got is None if want is None else isinstance(got, str) and got.startswith(want)


def run_node(code):
    esc = re.search(r"^function esc\(s\).*$", PAGE, re.M).group(0)
    helpers = PAGE[PAGE.index("/* safe-block:start"):PAGE.index("/* safe-block:end */")]
    r = subprocess.run(["node", "-e", esc + helpers + code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r


@node
def test_time_format_never_throws():
    vals = "[1795577286.197, '2026-11-25T05:28:06+00:00', null, undefined, NaN, 1e300, {}, [], true, '']"
    out = json.loads(run_node("console.log(JSON.stringify(%s.map(t0)))" % vals).stdout)
    assert out[0].startswith("2026-11-25 ") and len(out[0]) == 16
    assert out[1] == "2026-11-25 05:28"
    assert out[2:] == ["?"] * 8


@node
def test_a_block_that_throws_becomes_an_error_card_and_the_others_render():
    r = run_node("console.log(JSON.stringify([block('A',()=>'<div>a</div>'),block('B',()=>{null.x}),block('C',()=>'<div>c</div>'),"
                 "block('T',()=>{throw new Error('<bad>')},true)]))")
    a, b, c, t = json.loads(r.stdout)
    assert a == "<div>a</div>" and c == "<div>c</div>"
    assert "This block could not be drawn: " in b and 'data-block-error="B"' in b
    assert t.startswith('<div class="page">') and "&lt;bad&gt;" in t
    assert 'block "B" could not be drawn' in r.stderr   # logged to the console


def test_every_tab_and_the_queue_cards_are_guarded():
    assert "h+=block(R,V,true)" in PAGE
    assert "x.replace('T'" not in PAGE.replace(PAGE[PAGE.index("/* safe-block:start"):PAGE.index("/* safe-block:end */")], "")
    assert PAGE.count("block('Being processed now',vQueueLocal)") == 2
