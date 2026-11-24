"""logic.metadata_gaps on synthetic data: one test per kind of gap, one for clean data."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import copy
import datetime as dt
import json
import logic as L

NOW = dt.datetime(2026, 11, 24, 12, 0, tzinfo=dt.timezone.utc)
STAMP = "2026-11-24T10:00:00Z"  # 2 h before NOW
MODS = ["a", "a/one"]
POL = {"backup": "git", "retention": "kept"}


def card(kind, **extra):
    d = {"schema": 1, "kind": kind, "project": "demo", "generated_at": STAMP, "modules": {m: {"policy": POL} for m in MODS}}
    d.update(extra)
    return L.parse_card(json.dumps(d), kind, "demo", NOW)


def metrics(**mod):
    m = {"metrics": {"files_total": 2, "complexity": {"max": 3, "worst": [{}]}, "structure": {"files_in_cycles": 0}, "loc": {"python_source": {"files": 1, "code": 10}},
                     "test_coverage": {"pct_statements_covered": 80.0, "statements": 10, "data_file_modified": STAMP}}}
    m["metrics"].update(mod)
    return {"generated_at": STAMP, "modules": {"a": copy.deepcopy(m), "a/one": copy.deepcopy(m)}}


LINE = json.dumps({"ts": STAMP}) + "\n"
CFG = L.rules_config({"rules": {"groups": [{"name": "G", "paths": ["CLAUDE.md"]}]}})


def args(**over):
    a = dict(modules=MODS, cards={k: card(k) for k in L.CARD_KINDS}, metrics=metrics(), usage=dict(quota=LINE, transcript=LINE, proxy=LINE),
             policies=dict(config=CFG, files=["CLAUDE.md", "x.py"]))
    a.update(over)
    return a


def gaps(max_age_h=24, **over):
    return L.metadata_gaps("demo", NOW, max_age_h=max_age_h, **args(**over))


def kinds(gs):
    return sorted((g["kind"], g["module"]) for g in gs)


def test_clean_data_has_no_gaps():
    assert gaps() == []


def test_card_file_missing_is_a_gap():
    g = gaps(cards={**args()["cards"], "churn": L.parse_card(None, "churn")})
    assert [(x["kind"], x["module"]) for x in g] == [("card_data", None)] and "churn" in g[0]["what"]


def test_card_without_entry_for_a_module_is_a_gap():
    g = gaps(cards={**args()["cards"], "ambiguity": card("ambiguity", modules={"a": {}})})
    assert kinds(g) == [("card_data", "a/one")] and g[0]["age_h"] == 2.0


def test_old_card_data_is_stale_with_its_age():
    old = L.parse_card(json.dumps({"schema": 1, "kind": "churn", "project": "demo", "generated_at": "2026-11-23T10:00:00Z", "modules": {m: {} for m in MODS}}), "churn", "demo", NOW)
    g = gaps(cards={**args()["cards"], "churn": old})
    assert kinds(g) == [("stale", None)] and g[0]["age_h"] == 26.0


def test_age_limit_is_an_argument():
    assert gaps(max_age_h=1)  # 2-hour-old data is stale at a 1-hour limit
    assert gaps(max_age_h=3) == []


def test_snapshot_not_configured():
    g = gaps(metrics=None)
    assert kinds(g) == [("snapshot", None)] and "not wired" in g[0]["what"]


def test_module_missing_from_snapshot_is_not_wired():
    m = metrics()
    del m["modules"]["a/one"]
    assert kinds(gaps(metrics=m)) == [("snapshot", "a/one")]


def test_module_without_analysed_files_is_not_measured():
    m = metrics()
    m["modules"]["a/one"]["metrics"]["files_total"] = 0
    g = gaps(metrics=m)
    assert kinds(g) == [("snapshot", "a/one")] and "not measured" in g[0]["what"]


def test_python_module_without_coverage_figure():
    m = metrics()
    m["modules"]["a"]["metrics"]["test_coverage"]["pct_statements_covered"] = None
    g = gaps(metrics=m)
    assert kinds(g) == [("snapshot", "a")] and "coverage" in g[0]["what"]


def test_module_without_python_files_needs_no_coverage():
    m = metrics()
    m["modules"]["a"]["metrics"].update(loc={}, test_coverage={"pct_statements_covered": None}, complexity={}, structure={})
    assert gaps(metrics=m) == []


def test_old_snapshot_and_old_coverage_file_are_stale():
    m = metrics()
    m["generated_at"] = "2026-11-22T10:00:00Z"
    for x in m["modules"].values():
        x["metrics"]["test_coverage"]["data_file_modified"] = "2026-11-22T10:00:00Z"
    assert sorted(g["what"] for g in gaps(metrics=m)) == ["code snapshot is older than 24 hours", "coverage file is older than 24 hours"]


def test_missing_token_files_are_gaps():
    g = gaps(usage=dict(quota=None, transcript=None, proxy=None))
    assert [x["kind"] for x in g] == ["token_usage"] * 3


def test_empty_usage_file_is_a_gap():
    assert kinds(gaps(usage=dict(quota="\n", transcript=LINE, proxy=LINE))) == [("token_usage", None)]


def test_old_quota_history_and_transcript_are_stale_but_idle_proxy_is_not():
    old = json.dumps({"ts": "2026-11-20T10:00:00+00:00"}) + "\n"
    g = gaps(usage=dict(quota=old, transcript=old, proxy=old))
    assert [x["what"] for x in g] == ["quota history is older than 24 hours", "transcript usage file is older than 24 hours"]


def test_policies_not_configured_or_without_files():
    assert kinds(gaps(policies=dict(config=None, files=["CLAUDE.md"]))) == [("policies", None)]
    assert kinds(gaps(policies=dict(config=CFG, files=None))) == [("policies", None)]
    g = gaps(policies=dict(config=CFG, files=["x.py"]))
    assert kinds(g) == [("policies", None)] and "no files" in g[0]["what"]


def test_footprint_missing_backup_or_retention_is_a_gap():
    fp = card("footprint", modules={"a": {"policy": {"backup": "git", "retention": "undocumented"}}, "a/one": {"policy": {"backup": ""}}})
    g = gaps(cards={**args()["cards"], "footprint": fp})
    assert sorted(x["what"] for x in g if x["module"] == "a/one") == ["persistence footprint: no backup statement", "persistence footprint: no retention statement"]
    assert [x["what"] for x in g if x["module"] == "a"] == ["persistence footprint: no retention statement"]


def test_module_without_footprint_entry_is_a_gap():
    fp = card("footprint", modules={"a": {"policy": POL}})
    assert kinds(gaps(cards={**args()["cards"], "footprint": fp})) == [("card_data", "a/one")]


# ---- the gaps met in manual testing: each on a small fake project ----
def test_zero_percent_coverage_is_a_figure_not_a_gap():
    m = metrics()
    m["modules"]["a"]["metrics"]["test_coverage"]["pct_statements_covered"] = 0.0
    assert gaps(metrics=m) == []


def test_card_lines_without_data_complexity_cycles():
    m = metrics()
    m["modules"]["a"]["metrics"].update(complexity={}, structure={})
    g = gaps(metrics=m)
    assert sorted(x["what"] for x in g if x["module"] == "a") == ["module card: import cycles: no data", "module card: worst complexity: no data"]
    assert [x for x in g if x["module"] == "a/one"] == []


def test_empty_quality_tab_when_no_snapshot_is_configured():
    assert [(x["kind"], x["module"]) for x in gaps(metrics=None)] == [("snapshot", None)]


def test_churn_unassigned_counting_files_that_no_longer_exist():
    ch = card("churn", unassigned={"30": {"files": 0}, "90": {"files": 4}})
    g = gaps(cards={**args()["cards"], "churn": ch}, tracked=["a/x.py"], assign=[{"path": "a/", "module": "a"}])
    assert sorted(x["kind"] for x in g) == ["churn"] and "4 unassigned" in g[0]["what"]
    assert gaps(cards={**args()["cards"], "churn": ch}, tracked=["a/x.py", "b", "c", "d", "e"], assign=[{"path": "a/", "module": "a"}, {"path": "b", "module": None}, {"path": "c", "module": None}, {"path": "d", "module": None}, {"path": "e", "module": None}]) == []


def test_high_share_of_unsorted_files():
    am = card("ambiguity", modules={"a": {"own_files": 2, "sub_files": {}, "unsorted": [{"path": "x"}] * 2, "policy": POL}, "a/one": {"own_files": 9, "unsorted": []}})
    g = gaps(cards={**args()["cards"], "ambiguity": am})
    assert [(x["kind"], x["module"]) for x in g] == [("ambiguity", "a")] and "50%" in g[0]["what"]


def test_footprint_declared_file_missing_logger_without_destination_undeclared_data_file():
    fp = card("footprint", modules={"a": {"policy": POL, "files": [{"path": "~/d/demo/tok", "exists": False}, {"path": "~/d/demo/ok", "exists": True}],
                                         "logging": [{"logger": "x", "file": None, "rotation": ""}, {"logger": "y", "file": None, "rotation": "stderr"}]},
                                  "a/one": {"policy": POL}})
    g = gaps(cards={**args()["cards"], "footprint": fp}, data_root="~/d/demo", data_entries=["ok", "stray.db", "tok"])
    assert sorted(x["what"] for x in g) == [
        "declared file does not exist: ~/d/demo/tok", "file in the project's data folder not declared by any footprint: stray.db", "logger x: no file and no stated destination"]


def test_module_without_description_or_diagram():
    g = gaps(content={"a": {"txt": True, "svg": True}, "a/one": {}}, descs={})
    assert sorted(x["what"] for x in g if x["module"] == "a/one") == ["module has no description text (no docs/modules file, no desc in the map)", "module page has no diagram"]
    assert [x for x in gaps(content={"a": {"txt": True, "svg": True}, "a/one": {"svg": True}}, descs={"a/one": "d"})] == []


def test_policy_group_listing_no_files():
    cfg = L.rules_config({"rules": {"groups": [{"name": "G", "paths": ["CLAUDE.md"]}, {"name": "H", "paths": ["nothing/*.md"]}]}})
    g = gaps(policies=dict(config=cfg, files=["CLAUDE.md"]))
    assert [x["what"] for x in g] == ["Policies: group(s) list no files: H"]


def test_documents_without_a_modules_line():
    heads = {"docs/a.md": "# A\nModules: a\n", "docs/b.md": "# B\nno line\n", "docs/README.md": "# index"}
    assert [x["what"] for x in gaps(docs_heads=heads)] == ["document has no Modules: line: docs/b.md"]


def test_slow_or_failed_requests_are_gaps():
    g = gaps(timings=[("state", 0.4), ("usage", 3.5), ("code", None)])
    assert [x["what"] for x in g] == ["request usage took 3.5 s (limit 3 s)", "request code failed"]


def test_tracked_file_without_a_map_entry_is_a_gap_but_explicit_null_is_not():
    assert [x["kind"] for x in gaps(tracked=["a/x.py", "z.py"], assign=[{"path": "a/", "module": "a"}])] == ["map"]
    assert gaps(tracked=["a/x.py", "z.py"], assign=[{"path": "a/", "module": "a"}, {"path": "z.py", "module": None}]) == []


def test_quality_metric_without_threshold_or_band_and_module_without_any_bar():
    bar = {"band": "ok", "word": "normal"}
    full = {k: bar for k in L.QUALITY_METRICS}
    mod = lambda b, n=3: {"filesN": n, "bands": b}
    th = {k: {"direction": "low", "ok": 1, "warn": 2} for k in L.QUALITY_METRICS}
    assert gaps(quality=dict(modules={"a": mod(full)}, thresholds=th)) == []
    assert gaps(quality=dict(modules={"a": mod(full)}, thresholds=None)) == []   # defaults apply
    g = gaps(quality=dict(modules={"a": mod(full)}, thresholds={k: v for k, v in th.items() if k != "lint"}))
    assert [x["what"] for x in g] == ["quality metric lint has no band: no usable threshold in quality_thresholds"]
    g = gaps(quality=dict(modules={"a": mod({k: bar for k in L.QUALITY_METRICS if k != "dead"})}, thresholds=None))
    assert [x["what"] for x in g] == ["quality metric dead has no band in the code payload"]
    none = {k: {"band": "none", "word": "no figure"} for k in L.QUALITY_METRICS}
    g = gaps(quality=dict(modules={"a": mod(none), "a/one": mod(none, 0)}, thresholds=None))
    assert [(x["kind"], x["module"]) for x in g] == [("quality_bands", "a")]


def ret(text, min_days=3650):
    return dict(path="/home/u/.tool/settings.json", text=text, key="keepDays", min_days=min_days)


def test_transcript_retention_missing_or_short_is_a_gap_naming_the_settings_file():
    for text in (None, "{}", '{"keepDays": 30}', '{"keepDays": "long"}', '{"keepDays": true}', "not json", "[]"):
        g = gaps(retention=ret(text))
        assert kinds(g) == [("transcript_retention", None)], text
        assert "/home/u/.tool/settings.json" in g[0]["what"] and g[0]["age_h"] is None


def test_transcript_retention_at_or_above_the_minimum_is_clean():
    assert gaps(retention=ret('{"keepDays": 3650}')) == []
    assert gaps(retention=ret('{"keepDays": 99999, "other": 1}')) == []
    assert gaps(retention=ret('{"keepDays": 400}', min_days=365)) == []
    assert kinds(gaps(retention=ret('{"keepDays": 400}', min_days=500))) == [("transcript_retention", None)]


def test_transcript_retention_is_not_checked_for_a_project_without_the_tool():
    """No transcript usage file: the project does not use the tool, so the tool's settings are not its gap."""
    g = gaps(usage=dict(quota=LINE, transcript=None, proxy=LINE), retention=ret(None))
    assert "transcript_retention" not in {x["kind"] for x in g}
    assert gaps(retention=None) == []
