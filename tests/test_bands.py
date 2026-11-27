"""Quality bands: the pure banding function, the derived measures, and the /api/code payload of synthetic projects."""
import json

import pytest

import cockpit as C
from ckp.api import code as api_code, context as api_context
import logic as L

LOW, HIGH = dict(direction="low", ok=10, warn=20), dict(direction="high", ok=80, warn=50)


@pytest.mark.parametrize("value,expected", [(0, "ok"), (10, "ok"), (10.01, "warn"), (20, "warn"), (20.01, "bad"), (None, "none"), ("x", "none"), (True, "none")])
def test_band_low_direction_and_boundaries(value, expected):
    assert L.band_of(value, LOW) == dict(band=expected, word=L.BAND_WORDS[expected])


@pytest.mark.parametrize("value,expected", [(100, "ok"), (80, "ok"), (79.99, "warn"), (50, "warn"), (49.99, "bad"), (0, "bad"), (None, "none")])
def test_band_high_direction_and_boundaries(value, expected):
    assert L.band_of(value, HIGH)["band"] == expected


def test_words_per_band():
    assert [L.band_of(v, LOW)["word"] for v in (1, 15, 30, None)] == ["normal", "borderline", "out of range", "no figure"]


def test_every_metric_has_a_band_with_default_and_custom_thresholds():
    good = dict(cc=1, mi=90, code=1000, lint=0, dead=0, cyc=0, dup=0, doc=100, cov=100, testRatio=1.0, filesN=5)
    assert {k: b["band"] for k, b in L.module_bands(good).items()} == {k: "ok" for k in L.QUALITY_DEFAULTS}
    bad = dict(cc=99, mi=1, code=1000, lint=500, dead=50, cyc=3, dup=40, doc=0, cov=0, testRatio=0.0, filesN=5)
    assert {k: b["band"] for k, b in L.module_bands(bad).items()} == {k: "bad" for k in L.QUALITY_DEFAULTS}
    strict = {k: dict(v) for k, v in L.QUALITY_DEFAULTS.items()}
    strict["complexity"].update(ok=0, warn=0)
    assert L.module_bands(dict(good, cc=1), strict)["complexity"]["band"] == "bad"
    assert L.module_bands(dict(good, cc=1))["complexity"]["band"] == "ok"


def test_derived_measures_and_zero_coverage_is_a_figure():
    m = dict(code=2000, lint=20, dead=4, cov=0, filesN=2)  # 10 lint and 2 dead per 1,000 lines: both equal ok
    b = L.module_bands(m)
    assert b["lint"]["band"] == "ok" and b["dead"]["band"] == "ok"
    assert L.module_bands(dict(m, lint=21))["lint"]["band"] == "warn"
    assert b["coverage"]["band"] == "bad" and b["complexity"]["band"] == "none"


def test_nothing_analysed_or_no_code_lines_is_none():
    assert {b["band"] for b in L.module_bands(dict(cc=1, mi=50, cov=90, filesN=0)).values()} == {"none"}
    assert L.module_bands(dict(code=0, lint=5, dead=1, filesN=1))["lint"]["band"] == "none"


def _metrics(modules):
    return dict(generated_at="t", git_commit_short="c", modules={mid: dict(name=mid, metrics=x) for mid, x in modules.items()})


@pytest.mark.parametrize("ids", [("alpha", "beta/sub"), ("x1", "y2")])
def test_code_payload_carries_bands_for_every_module(ids, tmp_path, monkeypatch):
    full = dict(files_total=2, loc={"python_source": dict(files=2, code=1000)}, lint=dict(total=5), dead_code=dict(candidates=1),
                complexity=dict(max=12), test_coverage=dict(pct_statements_covered=0))
    (tmp_path / "metrics.json").write_text(json.dumps(_metrics({ids[0]: full, ids[1]: dict(files_total=0)})))
    monkeypatch.setattr(api_context, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(api_code, "biggest_by_module", lambda p, ids_: {})
    monkeypatch.setattr(api_context, "pk", lambda p, k: k)
    snap = C.code_snapshot(dict(code=str(tmp_path), name="synthetic"))
    a, b = snap["modules"][ids[0]]["bands"], snap["modules"][ids[1]]["bands"]
    assert set(a) == set(b) == set(L.QUALITY_DEFAULTS)
    assert (a["complexity"]["band"], a["lint"]["band"], a["coverage"]["band"], a["docstrings"]["band"]) == ("warn", "ok", "bad", "none")
    assert {x["band"] for x in b.values()} == {"none"}
    # a saved custom threshold changes the band
    f = C.card_settings_path(dict(name="synthetic", repo="o/r"))
    f.parent.mkdir(parents=True)
    f.write_text(json.dumps(dict(churn_colour="size", quality_thresholds=dict(complexity=dict(direction="low", ok=12, warn=20)))))
    assert C.code_snapshot(dict(code=str(tmp_path), name="synthetic"))["modules"][ids[0]]["bands"]["complexity"]["band"] == "ok"
