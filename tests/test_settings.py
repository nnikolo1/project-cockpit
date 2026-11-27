import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import json
import stat
import pytest
import cockpit as C
from ckp.api import context as api_context
import logic as L
from dispatcher.core import DEFAULTS, load_settings

P = dict(name="proj", repo="o/r")
OK = dict(DEFAULTS)


def test_validate_accepts_defaults_and_numeric_strings():
    assert L.validate_settings(OK, DEFAULTS, 6) == OK
    assert L.validate_settings({**OK, "workers": "4", "retries": "0"}, DEFAULTS, 6)["workers"] == 4


@pytest.mark.parametrize("key,val,word", [("runaway_minutes", 29, "30"), ("idle_minutes", 9, "10"), ("start_minutes", 0, "1"),
                                          ("workers", 7, "6"), ("workers", 0, "1"), ("retries", -1, "0"), ("interval_minutes", 0, "1"),
                                          ("interval_minutes", 1.5, "whole"), ("retries", True, "whole"), ("enabled", "yes", "on or off")])
def test_validate_refuses_with_a_clear_message(key, val, word):
    with pytest.raises(ValueError, match=word):
        L.validate_settings({**OK, key: val}, DEFAULTS, 6)


def test_validate_missing_key_is_refused():
    form = dict(OK)
    del form["retries"]
    with pytest.raises(ValueError):
        L.validate_settings(form, DEFAULTS, 6)


@pytest.mark.ui_action("server:settings")
def test_missing_file_shows_defaults_and_save_creates_it_private(tmp_path, monkeypatch):
    monkeypatch.setattr(api_context, "DATA_DIR", tmp_path)
    monkeypatch.setitem(C.CFG, "steer", True)
    assert C.get_settings(P)["values"] == DEFAULTS
    code, body = C.do_act(dict(action="settings", **{**OK, "workers": 2, "runaway_minutes": 50}), p=P)
    f = C.settings_path(P)
    assert code == 200 and body["msg"] == "settings saved" and f == tmp_path / "proj" / "dispatcher-settings.json"
    assert stat.S_IMODE(f.stat().st_mode) == 0o600 and not list(f.parent.glob("*.tmp"))
    assert load_settings(f)["workers"] == 2 and C.get_settings(P)["values"]["runaway_minutes"] == 50


@pytest.mark.ui_action("server:settings/refused")
def test_refused_value_is_400_and_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(api_context, "DATA_DIR", tmp_path)
    monkeypatch.setitem(C.CFG, "steer", True)
    assert C.do_act(dict(action="settings", **{**OK, "idle_minutes": 5}), p=P) == (400, dict(error="no-activity limit must be at least 10 minutes"))
    assert not C.settings_path(P).exists()


def test_read_only_refuses_save_with_403(tmp_path, monkeypatch):
    monkeypatch.setattr(api_context, "DATA_DIR", tmp_path)
    monkeypatch.setitem(C.CFG, "steer", False)
    assert C.do_act(dict(action="settings", **OK), p=P)[0] == 403
    assert not C.settings_path(P).exists() and C.get_settings(P)["steering"] is False


def test_card_settings_validation():
    assert L.validate_card_settings({"churn_colour": "absolute"}) == dict(L.CARD_SETTING_DEFAULTS, churn_colour="absolute")
    assert L.validate_card_settings({"churn_colour": "size"}) == L.CARD_SETTING_DEFAULTS
    for bad in ({}, {"churn_colour": "red"}, {"churn_colour": None}):
        with pytest.raises(ValueError, match="churn colour"):
            L.validate_card_settings(bad)


@pytest.mark.ui_action("server:cardsettings", "server:cardsettings/refused")
def test_card_settings_default_save_and_read_only(tmp_path, monkeypatch):
    monkeypatch.setattr(api_context, "DATA_DIR", tmp_path)
    monkeypatch.setitem(C.CFG, "steer", True)
    assert C.get_card_settings(P)["values"] == L.CARD_SETTING_DEFAULTS
    code, body = C.do_act(dict(action="cardsettings", churn_colour="absolute"), p=P)
    f = C.card_settings_path(P)
    assert code == 200 and body["msg"] == "card settings saved" and f == tmp_path / "proj" / "cockpit-settings.json"
    assert stat.S_IMODE(f.stat().st_mode) == 0o600 and not list(f.parent.glob("*.tmp"))
    assert C.get_card_settings(P)["values"] == dict(L.CARD_SETTING_DEFAULTS, churn_colour="absolute")
    assert C.do_act(dict(action="cardsettings", churn_colour="x"), p=P) == (400, dict(error="churn colour must be one of: size, absolute"))
    assert C.get_card_settings(P)["values"] == dict(L.CARD_SETTING_DEFAULTS, churn_colour="absolute")
    f.write_text("not json")
    assert C.get_card_settings(P)["values"] == L.CARD_SETTING_DEFAULTS
    assert not (tmp_path / "proj" / "dispatcher-settings.json").exists()
    monkeypatch.setitem(C.CFG, "steer", False)
    assert C.do_act(dict(action="cardsettings", churn_colour="size"), p=P)[0] == 403


def test_thresholds_roundtrip_and_validation(tmp_path, monkeypatch):
    monkeypatch.setattr(api_context, "DATA_DIR", tmp_path)
    monkeypatch.setitem(C.CFG, "steer", True)
    custom = {"coverage": dict(direction="high", ok=90, warn=70)}
    assert C.do_act(dict(action="cardsettings", churn_colour="size", quality_thresholds=custom), p=P)[0] == 200
    got = C.get_card_settings(P)["values"]["quality_thresholds"]
    assert got["coverage"] == custom["coverage"] and got["lint"] == L.QUALITY_DEFAULTS["lint"] and set(got) == set(L.QUALITY_DEFAULTS)
    for bad in ({"nosuch": dict(direction="low", ok=1, warn=2)},
                {"lint": dict(direction="high", ok=1, warn=2)},   # wrong direction
                {"lint": dict(direction="low", ok=5, warn=2)},    # ok above warn for low
                {"coverage": dict(direction="high", ok=50, warn=60)},  # ok below warn for high
                {"lint": dict(direction="low", ok="x", warn=2)}, {"lint": dict(direction="low", ok=True, warn=2)},
                {"lint": dict(direction="low", ok=1)}, {"lint": 3}, [1]):
        assert C.do_act(dict(action="cardsettings", churn_colour="size", quality_thresholds=bad), p=P)[0] == 400
    assert C.get_card_settings(P)["values"]["quality_thresholds"]["coverage"] == custom["coverage"]


def test_invalid_stored_threshold_falls_back_to_its_default(tmp_path, monkeypatch):
    monkeypatch.setattr(api_context, "DATA_DIR", tmp_path)
    f = C.card_settings_path(P)
    f.parent.mkdir(parents=True)
    f.write_text(json.dumps(dict(churn_colour="size", quality_thresholds=dict(
        lint=dict(direction="low", ok=9, warn=1), coverage=dict(direction="high", ok=90, warn=70), bogus=1))))
    t = C.get_card_settings(P)["values"]["quality_thresholds"]
    assert t["lint"] == L.QUALITY_DEFAULTS["lint"] and t["coverage"]["ok"] == 90 and "bogus" not in t
