import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import json
import stat
import pytest
import cockpit as C
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


def test_missing_file_shows_defaults_and_save_creates_it_private(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "DATA_DIR", tmp_path)
    monkeypatch.setitem(C.CFG, "steer", True)
    assert C.get_settings(P)["values"] == DEFAULTS
    code, body = C.do_act(dict(action="settings", **{**OK, "workers": 2, "runaway_minutes": 50}), p=P)
    f = C.settings_path(P)
    assert code == 200 and f == tmp_path / "proj" / "dispatcher-settings.json"
    assert stat.S_IMODE(f.stat().st_mode) == 0o600 and not list(f.parent.glob("*.tmp"))
    assert load_settings(f)["workers"] == 2 and C.get_settings(P)["values"]["runaway_minutes"] == 50


def test_refused_value_is_400_and_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "DATA_DIR", tmp_path)
    monkeypatch.setitem(C.CFG, "steer", True)
    assert C.do_act(dict(action="settings", **{**OK, "idle_minutes": 5}), p=P)[0] == 400
    assert not C.settings_path(P).exists()


def test_read_only_refuses_save_with_403(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "DATA_DIR", tmp_path)
    monkeypatch.setitem(C.CFG, "steer", False)
    assert C.do_act(dict(action="settings", **OK), p=P)[0] == 403
    assert not C.settings_path(P).exists() and C.get_settings(P)["steering"] is False


def test_card_settings_validation():
    assert L.validate_card_settings({"churn_colour": "absolute"}) == {"churn_colour": "absolute"}
    assert L.validate_card_settings({"churn_colour": "size"}) == L.CARD_SETTING_DEFAULTS
    for bad in ({}, {"churn_colour": "red"}, {"churn_colour": None}):
        with pytest.raises(ValueError, match="churn colour"):
            L.validate_card_settings(bad)


def test_card_settings_default_save_and_read_only(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "DATA_DIR", tmp_path)
    monkeypatch.setitem(C.CFG, "steer", True)
    assert C.get_card_settings(P)["values"] == L.CARD_SETTING_DEFAULTS
    code, _ = C.do_act(dict(action="cardsettings", churn_colour="absolute"), p=P)
    f = C.card_settings_path(P)
    assert code == 200 and f == tmp_path / "proj" / "cockpit-settings.json"
    assert stat.S_IMODE(f.stat().st_mode) == 0o600 and not list(f.parent.glob("*.tmp"))
    assert C.get_card_settings(P)["values"] == {"churn_colour": "absolute"}
    assert C.do_act(dict(action="cardsettings", churn_colour="x"), p=P)[0] == 400
    assert C.get_card_settings(P)["values"] == {"churn_colour": "absolute"}
    f.write_text("not json")
    assert C.get_card_settings(P)["values"] == L.CARD_SETTING_DEFAULTS
    assert not (tmp_path / "proj" / "dispatcher-settings.json").exists()
    monkeypatch.setitem(C.CFG, "steer", False)
    assert C.do_act(dict(action="cardsettings", churn_colour="size"), p=P)[0] == 403
