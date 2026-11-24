import json
import pathlib
import re
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).parent.parent
PAGE = (ROOT / "web" / "index.html").read_text()
node = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")


def contract_keys():
    return re.findall(r"^\| `(\w+)` \|", (ROOT / "docs" / "contracts" / "quality-bands.md").read_text(), re.M)


def helpers():
    return PAGE[PAGE.index("/* quality-bands:start"):PAGE.index("/* quality-bands:end */")]


def run_node(code):
    esc = re.search(r"^function esc\(s\).*$", PAGE, re.M).group(0)
    r = subprocess.run(["node", "-e", esc + helpers() + code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


@node
def test_page_script_parses(tmp_path):
    script = re.findall(r"<script>(.*?)</script>", PAGE, re.S)[-1]
    f = tmp_path / "page.js"
    f.write_text(script)
    assert subprocess.run(["node", "--check", str(f)], capture_output=True).returncode == 0


@node
@pytest.mark.parametrize("bands", [
    {"a": "ok", "b": "warn"},       # one synthetic module, bands mixed
    {"a": "bad", "b": "none"},      # another one, other bands
])
def test_a_bar_for_every_contract_metric(bands):
    keys = contract_keys()
    assert len(keys) == 9
    mod = {"bands": {k: {"band": ["ok", "warn", "bad", "none"][i % 4], "word": "w%d" % i} for i, k in enumerate(keys)}}
    if "bad" in bands.values():
        mod["bands"].pop(keys[0])   # a missing band falls back to grey
    out = json.loads(run_node("console.log(JSON.stringify(%s.map(k=>qTile(%s,k,'L',1,'s'))))" % (json.dumps(keys), json.dumps(mod))))
    for k, html in zip(keys, out):
        assert 'data-q="%s"' % k in html and 'class="qbar qb-' in html and "qw" in html
    assert 'data-band="none"' in out[0] if "bad" in bands.values() else 'data-band="ok"' in out[0]


@node
def test_page_asks_for_every_contract_metric_and_has_a_legend():
    assert set(re.findall(r"qTile\(c,'(\w+)'", PAGE)) == set(contract_keys())
    assert "colours follow the thresholds in Settings" in PAGE and "no pass or fail until" not in PAGE
    assert "green" in run_node("console.log(qLegend())") and "grey" in run_node("console.log(qLegend())")
