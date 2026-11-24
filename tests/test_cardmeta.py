import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import datetime as dt
import json
import shutil
import cockpit as C
import logic as L

FIX = pathlib.Path(__file__).parent / "fixtures" / "cardmeta"
NOW = dt.datetime(2026, 11, 20, 12, 0, tzinfo=dt.timezone.utc)  # 6 h after the fixtures were generated


def texts():
    return {k: (FIX / f"{k}.json").read_text() for k in L.CARD_KINDS}


def test_parse_card_ok_has_age_and_is_not_stale():
    c = L.parse_card(texts()["churn"], "churn", "demo", NOW)
    assert c["status"] == "ok" and c["age_h"] == 6.0 and not c["stale"] and c["generated_at"] == "2026-11-20T06:00:00Z"


def test_parse_card_stale_after_48_hours():
    assert L.parse_card(texts()["churn"], "churn", "demo", NOW + dt.timedelta(hours=43))["stale"]
    assert not L.parse_card(texts()["churn"], "churn", "demo", NOW + dt.timedelta(hours=41))["stale"]


def test_parse_card_missing_and_foreign_files_mean_no_data_never_raise():
    t = texts()["churn"]
    assert L.parse_card(None, "churn")["status"] == "missing"
    for bad in ("", "{", "[]", "null", '{"schema": 2, "kind": "churn"}', '{"schema": 1, "kind": "footprint"}'):
        assert L.parse_card(bad, "churn")["status"] == "invalid", bad
    assert "project" in L.parse_card(t, "churn", "other")["reason"]


def test_parse_card_unreadable_timestamp_is_stale_not_fatal():
    c = L.parse_card('{"schema":1,"kind":"churn","project":"p","generated_at":"yesterday"}', "churn", "p", NOW)
    assert c["status"] == "ok" and c["stale"] and c["age_h"] is None


def test_rank_bands_top_fifth_hot_bottom_fifth_cold_zero_dormant():
    vals = {c: i for i, c in enumerate("abcdefghij", 1)} | {"z": 0, "n": None}
    b = L.rank_bands(vals)
    assert (b["j"], b["i"], b["a"], b["b"], b["e"], b["z"], b["n"]) == ("hot", "hot", "cold", "cold", "mid", "dormant", None)


def test_rank_bands_ties_and_tiny_sets_stay_mid():
    assert set(L.rank_bands({"a": 3, "b": 3, "c": 3}).values()) == {"mid"}
    assert set(L.rank_bands({"a": 1, "b": 9}).values()) == {"mid"}
    assert L.rank_bands({}) == {} and L.rank_bands({"a": 0}) == {"a": "dormant"}


def test_churn_view_ranks_per_level_and_computes_relative_churn():
    v = L.churn_view(json.loads(texts()["churn"]))
    m = v["modules"]
    assert v["windows"] == ["30", "90"]
    assert m["module-a"]["windows"]["30"]["churn"] == 1000          # total, not own
    assert m["module-a"]["windows"]["30"]["rel"] == 0.4            # 1000 / (2000 + 500)
    assert m["module-b"]["windows"]["30"]["band_abs"] == "dormant" and m["module-b"]["windows"]["30"]["band_rel"] == "dormant"
    assert m["module-a/one"]["windows"]["30"]["band_abs"] == "mid"  # alone on its level
    assert m["module-c"]["windows"]["30"]["rel"] == 0.5 and "90" not in m["module-c"]["windows"]
    assert v["unassigned"]["30"]["commits"] == 1


def test_churn_view_without_size_has_no_relative_value():
    v = L.churn_view({"modules": {"x": {"total": {"30": {"churn": 5}}}}})
    assert v["modules"]["x"]["windows"]["30"]["rel"] is None


def test_file_location_and_policy_gap():
    assert [L.file_location(p) for p in ("data/a.csv", "/var/x", "~/x", "../x", "C:\\x")] == ["inside", "outside", "outside", "outside", "outside"]
    assert L.policy_text("nightly copy") == ("nightly copy", False)
    assert L.policy_text(None) == ("undocumented", True) and L.policy_text("Undocumented") == ("Undocumented", True)


def test_footprint_view_marks_locations_and_gaps():
    a = L.footprint_view(json.loads(texts()["footprint"]))["modules"]
    assert [f["location"] for f in a["module-a"]["files"]] == ["inside", "outside"]
    assert a["module-a"]["backup"][1] is False and a["module-a"]["retention"] == ("undocumented", True)
    assert a["module-b"]["backup"] == ("undocumented", True) and a["module-b"]["tables"] == []


def test_ambiguity_view_counts_share_and_flags_high():
    v = L.ambiguity_view(json.loads(texts()["ambiguity"]))
    a = v["modules"]["module-a"]
    assert (a["total"], a["share"], a["high"]) == (12, 0.25, False)  # 6 + 3 + 0 + 3 unsorted
    assert L.ambiguity_view(json.loads(texts()["ambiguity"]), warn=0.2)["modules"]["module-a"]["high"]
    assert not v["modules"]["module-b"]["high"] and v["module_level"][0]["path"] == "common/helpers.py" and v["unassigned_nondoc"] == 4


def test_ambiguity_view_empty_module_is_not_high():
    assert not L.ambiguity_view({"modules": {"x": {}}})["modules"]["x"]["high"]


def test_card_meta_mixes_ok_missing_and_broken():
    t = texts() | {"footprint": None, "ambiguity": "{"}
    out = L.card_meta(t, "demo", NOW)
    assert out["churn"]["status"] == "ok" and "view" in out["churn"] and "data" not in out["churn"]
    assert out["footprint"]["status"] == "missing" and out["ambiguity"]["status"] == "invalid"


def test_card_meta_content_of_the_wrong_shape_is_invalid_not_a_crash():
    bad = json.dumps(dict(schema=1, kind="churn", project="demo", generated_at="2026-11-20T06:00:00Z", modules=[1]))
    assert L.card_meta(dict(churn=bad), "demo", NOW)["churn"]["status"] == "invalid"


def test_meta_dir_default_and_override():
    assert C.meta_dir(dict(name="widgets", repo="acme/widgets")) == C.DATA_DIR / "widgets" / "cardmeta"
    assert C.meta_dir(dict(name="widgets", repo="acme/widgets", meta="/x/y")) == pathlib.Path("/x/y")


def test_projects_file_accepts_optional_meta():
    ps, _ = C.projects_from_file({"projects": [{"name": "a", "repo": "o/a", "meta": "~/m"}, {"name": "b", "repo": "o/b"}]})
    assert ps[0]["meta"].endswith("/m") and "meta" not in ps[1]


def test_card_metadata_reads_the_folder_and_survives_an_empty_one(tmp_path):
    shutil.copytree(FIX, tmp_path / "m")
    p = dict(name="demo", repo="o/demo", meta=str(tmp_path / "m"))
    out = C.card_metadata(p, NOW)
    assert {k: out[k]["status"] for k in L.CARD_KINDS} == dict(footprint="ok", churn="ok", ambiguity="ok")
    out = C.card_metadata(dict(p, meta=str(tmp_path / "none")), NOW)
    assert {out[k]["status"] for k in L.CARD_KINDS} == {"missing"}
    (tmp_path / "m" / "churn.json").write_bytes(b"\xff\xfe")
    assert C.card_metadata(p, NOW)["churn"]["status"] == "invalid"


def test_ambiguity_view_lists_the_files_ambiguous_between_sub_modules_as_notes():
    v = L.ambiguity_view({"modules": {"m": {"own_files": 1, "sub_files": {"a": 2}, "unsorted": [
        {"path": "x.py", "candidates": ["m/a", "m/b"], "reason": "shared"}, {"path": "y.py", "candidates": [], "reason": ""}]}}})
    m = v["modules"]["m"]
    assert [u["path"] for u in m["ambiguous"]] == ["x.py"] and len(m["unsorted"]) == 2


def _wk(n, **k):
    return [dict(w=f"2026-{i:02d}", commits=k.get("c", 1), added=k.get("a", 10), removed=k.get("r", 5), tests=k.get("t", 3), docs=k.get("d", 2)) for i in range(n)]


def test_changes_view_ranks_by_30_days_with_subs_under_their_parent():
    mods = {"a": {"total": {"30": {"churn": 5}}, "series": {"weekly": {"total": _wk(52)}}},
            "b": {"total": {"30": {"churn": 50}}, "series": {"weekly": {"total": _wk(52)}}},
            "a/x": {"own": {"30": {"churn": 1}}, "series": {"weekly": {"own": _wk(52)}}},
            "a/y": {"own": {"30": {"churn": 4}}, "series": {"weekly": {"own": _wk(52)}}}}
    assert [p["id"] for p in L.changes_view(mods)] == ["b", "a", "a/y", "a/x"]
    assert [p["level"] for p in L.changes_view(mods)] == [0, 0, 1, 1]


def test_changes_view_sums_and_splits_source_tests_docs():
    p = L.changes_view({"a": {"series": {"weekly": {"total": _wk(60)}}}})[0]
    assert len(p["weeks"]) == 52 and p["churn52"] == 52 * 15 and p["commits90"] == 13
    assert p["weeks"][0]["src"] == 15 - 3 - 2


def test_changes_view_missing_or_broken_series_is_flagged_not_fatal():
    ps = L.changes_view({"a": {"total": {"30": {"churn": 7}}}, "b": {"series": "x"}, "c": {"series": {"weekly": {"total": "no"}}}, "d": 3})
    assert [p["id"] for p in ps] == ["a", "b", "c"] and all(p["weeks"] is None for p in ps) and ps[0]["churn30"] == 7


def test_changes_view_sub_uses_own_and_top_uses_total_and_is_in_churn_view():
    m = {"a": {"series": {"weekly": {"own": _wk(2, a=1), "total": _wk(3)}}}, "a/x": {"series": {"weekly": {"own": _wk(4), "total": _wk(5)}}}}
    ps = L.changes_view(m)
    assert len(ps[0]["weeks"]) == 3 and len(ps[1]["weeks"]) == 4
    assert [p["id"] for p in L.churn_view({"modules": m})["changes"]] == ["a", "a/x"]
