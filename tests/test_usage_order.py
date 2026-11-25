"""Token Usage: the cards appear in the order of the one list USAGE_ORDER (owner, 2026-11-24: the 5-hour windows on top); an absent card leaves no gap."""
import json
import pathlib
import re
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).parent.parent
PAGE = (ROOT / "web" / "index.html").read_text()
node = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")

ORDER = ["windows", "heatmap", "readings", "sessions", "days", "quota", "projects", "fit", "tasks"]
TITLE = {"windows": "Usage per 5-hour window", "heatmap": "Tokens per day", "readings": "Today’s readings", "sessions": "Interactive sessions",
         "days": "Usage per day", "quota": "Vendor quota used", "projects": "Per project, by month", "fit": "How these numbers fit", "tasks": "Per task"}


def usage_order():
    return json.loads(re.search(r"^const USAGE_ORDER=(\[.*?\]);", PAGE, re.M).group(1).replace("'", '"'))


def titles(use, blank=()):
    """The card titles vUsage() renders for the payload `use`, with the card functions named in `blank` returning nothing."""
    code = "\n".join([re.search(r"^function esc\(s\).*$", PAGE, re.M).group(0),
                      PAGE[PAGE.index("const USEF="):PAGE.index("function vRules(){")],
                      "function heatmap(){return ''}", "".join("%s=()=>'';" % f for f in blank),
                      "var USE=%s;process.stdout.write(vUsage())" % json.dumps(use)])
    r = subprocess.run(["node", "-e", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.count("<div") == r.stdout.count("</div>")
    return [re.sub(r"<.*", "", t) for t in re.findall(r'<div class="card"><h2>(.*?)</h2>', r.stdout)]


def test_the_order_is_the_one_list():
    assert usage_order() == ORDER
    v = PAGE[PAGE.index("function vUsage(){"):PAGE.index("function vRules(){")]
    assert "USAGE_ORDER.map(" in v and v.count("return head+") == 2  # the no-data page and the list; no second order


@node
def test_cards_render_in_the_list_order():
    t = titles({})
    first = [next(i for i, x in enumerate(t) if x.startswith(TITLE[k])) for k in ORDER]
    assert first == sorted(first) and t[0] == TITLE["windows"]


@node
def test_an_absent_card_leaves_no_gap():
    t = titles({}, blank=("usageTable", "projectsCard"))
    assert t[0] == TITLE["heatmap"] and not any(x.startswith(("Usage per", "Per project")) for x in t) and t[-1] == TITLE["tasks"]
