import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import datetime as dt
import logic as L
import cockpit as C

NOW = dt.datetime(2026, 11, 20, 12, 0, tzinfo=dt.timezone.utc)


def iss(n, labels=("question", "type:decision"), state="open", title=None, body="", created="2026-11-10T12:00:00Z"):
    return dict(number=n, title=title or f"[AB-{n:02d}] Which option for item {n}?", url=f"https://x/issues/{n}", state=state,
                created_at=created, labels=list(labels), body=body)


def test_recommendation_extracted_and_collapsed():
    body = "**Question.** Q?\n\n**Recommendation.** Do the first\nthing, it is simpler.\n\n**Context.** more"
    assert L.parse_recommendation(body) == "Do the first thing, it is simpler."


def test_recommendation_variants_and_absence():
    assert L.parse_recommendation("**Recommendation** Go.") == "Go."
    assert L.parse_recommendation("**Recommendation.** last paragraph, no blank line after") == "last paragraph, no blank line after"
    assert L.parse_recommendation("**Question.** only") is None
    assert L.parse_recommendation("**Recommendation.**\n\n**Context.** x") is None
    assert L.parse_recommendation(None) is None


def test_recommendation_capped():
    r = L.parse_recommendation("**Recommendation.** " + "word " * 200)
    assert len(r) == L.RECO_CAP and r.endswith("…")


def test_age_days():
    assert L.age_days("2026-11-10T12:00:00Z", NOW) == 10
    assert L.age_days("2026-11-20T11:00:00Z", NOW) == 0
    assert L.age_days("2026-12-01T00:00:00Z", NOW) == 0  # a clock skew never gives a negative age


def test_parse_questions_filters_and_splits_title():
    qs = L.parse_questions([iss(1), iss(2, labels=("type:decision",)), iss(3, state="closed"),
                            iss(4, title="No series here", body="**Recommendation.** Yes.")], "alpha")
    assert [q["number"] for q in qs] == [1, 4]
    assert qs[0]["key"] == "AB-01" and qs[0]["title"] == "Which option for item 1?" and qs[0]["project"] == "alpha"
    assert qs[1]["key"] == "#4" and qs[1]["title"] == "No series here" and qs[1]["recommendation"] == "Yes."


def test_merge_sorts_oldest_first_across_projects_with_age():
    a = L.parse_questions([iss(5, created="2026-11-15T00:00:00Z"), iss(6, created="2026-11-01T00:00:00Z")], "alpha")
    b = L.parse_questions([iss(2, created="2026-11-05T00:00:00Z")], "beta")
    rows = L.merge_questions({"alpha": a, "beta": b}, NOW)
    assert [(q["project"], q["number"]) for q in rows] == [("alpha", 6), ("beta", 2), ("alpha", 5)]
    assert rows[0]["age"] == 19
    assert L.group_questions(rows) == {"alpha": 2, "beta": 1}


def test_merge_ties_are_stable_and_empty_ok():
    same = "2026-11-05T00:00:00Z"
    rows = L.merge_questions({"b": L.parse_questions([iss(1, created=same)], "b"), "a": L.parse_questions([iss(2, created=same)], "a")}, NOW)
    assert [q["project"] for q in rows] == ["a", "b"]
    assert L.merge_questions({}, NOW) == [] and L.group_questions([]) == {}


def test_build_questions_reports_unreadable_project(monkeypatch):
    good = dict(questions=L.parse_questions([iss(1)], "alpha"), source=dict(fetchedAt=C.time.time() - 30))

    def fake(fresh=False, p=None):
        if p["repo"] == "o/bad":
            raise RuntimeError("rate limit")
        return good

    monkeypatch.setattr(C, "PROJECTS", {"alpha": dict(repo="o/alpha"), "beta": dict(repo="o/bad")})
    monkeypatch.setattr(C, "get_state", fake)
    out = C.build_questions(now=NOW)
    assert [q["number"] for q in out["questions"]] == [1]
    by = {p["name"]: p for p in out["projects"]}
    assert by["alpha"]["error"] is None and 29 <= by["alpha"]["age_s"] <= 40
    assert by["beta"]["error"] == "rate limit" and by["beta"]["age_s"] is None
