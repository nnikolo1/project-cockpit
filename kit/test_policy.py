from policy import decide

M = "mgr"
_n = [0]


def ev(kind, label, actor, issue=1):
    _n[0] += 1
    return {"id": _n[0], "event": kind, "label": {"name": label}, "actor": {"login": actor},
            "issue": {"number": issue}, "created_at": f"2026-11-17T10:00:{_n[0]:02d}Z"}


def test_manager_release_kept():
    assert decide([ev("labeled", "released", M)], M) == []


def test_forged_release_removed():
    assert decide([ev("labeled", "released", "mallory")], M) == [(1, "released", "remove", "mallory")]


def test_forge_after_manager_release_noop_if_still_on():
    # a bot "labeling" an already-present label does not change state
    assert decide([ev("labeled", "released", M), ev("labeled", "released", "bot")], M) == []


def test_manager_removal_stays_removed_and_forged_readd_reverted():
    es = [ev("labeled", "released", M), ev("unlabeled", "released", M), ev("labeled", "released", "bot")]
    assert decide(es, M) == [(1, "released", "remove", "bot")]


def test_non_manager_removal_of_release_restored():
    es = [ev("labeled", "released", M), ev("unlabeled", "released", "bot")]
    assert decide(es, M) == [(1, "released", "add", "bot")]


def test_priority_changed_twice():
    es = [ev("labeled", "P2", M), ev("unlabeled", "P2", M), ev("labeled", "P1", M),
          ev("labeled", "P3", "bot"), ev("unlabeled", "P1", "bot")]
    assert decide(es, M) == [(1, "P1", "add", "bot"), (1, "P3", "remove", "bot")]


def test_priority_manager_changes_twice_is_fine():
    es = [ev("labeled", "P1", M), ev("unlabeled", "P1", M), ev("labeled", "P2", M)]
    assert decide(es, M) == []


def test_github_actions_bot_all_manager_only_labels():
    es = [ev("labeled", x, "github-actions[bot]", 7) for x in ("released", "P1", "cancelled")]
    assert [a[:3] for a in decide(es, M)] == [(7, "P1", "remove"), (7, "cancelled", "remove"), (7, "released", "remove")]


def test_unprotected_label_ignored_and_issues_independent():
    es = [ev("labeled", "must", "bot"), ev("labeled", "released", "bot", 2), ev("labeled", "released", M, 3)]
    assert decide(es, M) == [(2, "released", "remove", "bot")]
