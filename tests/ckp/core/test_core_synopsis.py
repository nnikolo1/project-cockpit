"""ckp.core.synopsis: the dated session synopsis of a project, as a Worker gets it (PC-70)."""
import datetime as dt

from ckp.core import synopsis as S

TODAY = dt.date(2026, 11, 25)


def test_date_is_read_from_the_first_line_only():
    assert S.synopsis_date("Synopsis of 2026-11-24\nbody") == dt.date(2026, 11, 24)
    assert S.synopsis_date("Synopsis of 2026.11.24") == dt.date(2026, 11, 24)
    assert S.synopsis_date("title\nSynopsis of 2026-11-24") is None
    assert S.synopsis_date("Synopsis of 2026-13-40") is None
    assert S.synopsis_date(None) is None


def test_fresh_synopsis_is_copied_unchanged():
    t = "Synopsis of 2026-11-18\nbody"   # 7 days: still fresh
    assert S.inbox_text(t, TODAY) == t


def test_old_synopsis_is_marked_stale_with_its_age():
    assert S.inbox_text("Synopsis of 2026-11-17\nbody", TODAY) == "STALE: 8 days old\nSynopsis of 2026-11-17\nbody"
    assert S.inbox_text("Synopsis of 2026-11-17\nbody", TODAY, stale_days=10).startswith("Synopsis")


def test_undated_synopsis_is_marked_stale():
    assert S.inbox_text("body", TODAY) == "STALE: undated\nbody"


def test_gaps():
    assert S.gaps(None, TODAY)[0][1] is None
    assert S.gaps("Synopsis of 2026-11-01", TODAY) == [("session synopsis is 24 days old (limit 7)", 576.0)]
    assert S.gaps("Synopsis of 2026-11-24", TODAY) == []
