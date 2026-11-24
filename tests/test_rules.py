import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import json
import logic as L
import cockpit as C

CFG = {"rules": {"groups": [{"name": "Instructions", "paths": ["CLAUDE.md", "**/CLAUDE.md"]},
                            {"name": "Orchestration", "paths": [".claude/**", ".github/workflows/policy.yml"]},
                            {"name": "Decisions", "paths": ["docs/adr/*.md"]}], "managers": ["Ann Lee", "boss@example.org"]}}
RC = L.rules_config(CFG)
G = RC["groups"]


def rec(sha, date, name, email, subj, body, rest):
    return f"{L.RS}{sha}{L.US}{date}T10:00:00+00:00{L.US}{name}{L.US}{email}{L.US}{subj}{L.US}{body}{L.GS}\n{rest}"


def test_glob_matching():
    r = L.glob_re("**/CLAUDE.md")
    assert r.match("CLAUDE.md") and r.match("a/b/CLAUDE.md") and not r.match("a/CLAUDE.md.bak")
    assert L.glob_re(".claude/**").match(".claude/skills/x/SKILL.md")
    assert not L.glob_re("docs/adr/*.md").match("docs/adr/sub/x.md") and L.glob_re("docs/adr/*.md").match("docs/adr/001.md")
    assert L.glob_re("a?.md").match("ab.md") and not L.glob_re("a?.md").match("a/.md")
    assert not L.glob_re("a.md").match("axmd")


def test_grouping_first_match_wins():
    assert L.rule_group("sub/CLAUDE.md", G) == "Instructions"
    assert L.rule_group(".claude/agents/a.md", G) == "Orchestration"
    assert L.rule_group("src/main.py", G) is None


def test_agent_and_manager_detection():
    assert L.is_agent_assisted("fix\n\nCo-Authored-By: Claude X <noreply@anthropic.com>", "Ann Lee", "a@x")
    assert L.is_agent_assisted("", "dependabot[bot]", "d@x") and L.is_agent_assisted("", "x", "noreply@anthropic.com")
    assert not L.is_agent_assisted("plain message", "Ann Lee", "a@x")
    assert L.is_manager("ann lee", "z@z", RC["managers"]) and L.is_manager("Q", "BOSS@example.org", RC["managers"])
    assert not L.is_manager("Bob", "bob@x", RC["managers"])


def test_bots_default_and_custom():
    assert RC["bots"] == list(L.BOT)
    custom = L.rules_config({"rules": {**CFG["rules"], "bots": ["MyAgent"]}})
    assert custom["bots"] == ["myagent"]
    assert L.is_agent_assisted("", "MyAgent", "m@x", custom["bots"])
    assert not L.is_agent_assisted("", "dependabot[bot]", "d@x", custom["bots"])


def test_numstat_and_diff_excerpt():
    assert L.parse_numstat("3\t1\tCLAUDE.md\n-\t-\tdocs/adr/1.md\n5\t5\tsrc/a.py\n", G) == [
        {"path": "CLAUDE.md", "group": "Instructions", "add": 3, "del": 1}, {"path": "docs/adr/1.md", "group": "Decisions", "add": 0, "del": 0}]
    d = "diff --git a/CLAUDE.md b/CLAUDE.md\n--- a/CLAUDE.md\n+++ b/CLAUDE.md\n@@ -1 +1 @@\n-old\n+new\ndiff --git a/src/a.py b/src/a.py\n+++ b/src/a.py\n+code\n"
    assert L.diff_excerpt(d, G) == [["@", "CLAUDE.md"], ["-", "old"], ["+", "new"]]


def test_diff_excerpt_cap_and_line_length():
    d = "diff --git a/CLAUDE.md b/CLAUDE.md\n" + "".join(f"+line{i}\n" for i in range(50)) + "+" + "x" * 500 + "\n"
    ex = L.diff_excerpt(d, G)
    assert len([x for x in ex if x[0] == "+"]) == 30
    assert len(L.diff_excerpt("diff --git a/CLAUDE.md b/CLAUDE.md\n+" + "x" * 500, G)[1][1]) == L.LINE_CAP


def test_rule_changes_and_needs_attention():
    num = (rec("a" * 40, "2026-11-10", "Ann Lee", "a@x", "tighten rule", "Co-Authored-By: Claude <noreply@anthropic.com>", "2\t1\tCLAUDE.md\n")
           + rec("b" * 40, "2026-11-09", "Ann Lee", "a@x", "manual edit", "", "1\t0\t.claude/s.md\n")
           + rec("c" * 40, "2026-11-08", "Bob", "b@x", "by other", "", "1\t1\tdocs/adr/2.md\n")
           + rec("d" * 40, "2026-11-07", "Ann Lee", "a@x", "code only", "", "9\t9\tsrc/a.py\n"))
    diff = rec("a" * 40, "2026-11-10", "Ann Lee", "a@x", "tighten rule", "", "diff --git a/CLAUDE.md b/CLAUDE.md\n-x\n+y\n")
    ch = L.parse_rule_changes(num, diff, G, RC["managers"])
    assert [c["sha"] for c in ch] == ["aaaaaaa", "bbbbbbb", "ccccccc"]
    a, b, c = ch
    assert a["agent_assisted"] and a["by_manager"] and a["needs_attention"] and a["co_authors"] == ["Claude"] and a["diff"][-1] == ["+", "y"]
    assert not b["agent_assisted"] and b["by_manager"] and not b["needs_attention"] and b["diff"] == []
    assert not c["by_manager"] and c["needs_attention"] and c["date"] == "2026-11-08"


def test_last_changes_and_files():
    txt = rec("a" * 40, "2026-11-10", "Ann", "a@x", "new", "", "CLAUDE.md\nsrc/a.py\n") + rec("b" * 40, "2026-01-01", "Bob", "b@x", "old", "", "CLAUDE.md\ndocs/adr/1.md\n")
    last = L.parse_last_changes(txt, G)
    assert last["CLAUDE.md"]["sha"] == "aaaaaaa" and last["docs/adr/1.md"]["author"] == "Bob" and "src/a.py" not in last
    fs = L.rule_files(["docs/adr/1.md", "src/a.py", "CLAUDE.md", "docs/adr/0.md"], last, G)
    assert [f["path"] for f in fs] == ["CLAUDE.md", "docs/adr/0.md", "docs/adr/1.md"] and fs[1]["last"] is None


def test_pending_prs_filtered_by_paths():
    prs = [dict(number=1, title="t", author={"login": "u"}, url="u1", createdAt="2026-11-01T00:00:00Z", files=[{"path": "CLAUDE.md"}, {"path": "x.py"}]),
           dict(number=2, title="t2", author=None, url="u2", files=[{"path": "x.py"}])]
    out = L.pending_rule_prs(prs, G)
    assert len(out) == 1 and out[0]["number"] == 1 and out[0]["files"] == [{"path": "CLAUDE.md", "group": "Instructions"}] and out[0]["created"] == "2026-11-01"


def test_not_configured():
    assert L.rules_config(None) is None and L.rules_config({}) is None and L.rules_config({"rules": {"groups": []}}) is None
    C.CFG.update(modules=None, git=None)
    assert C.build_rules() == {"configured": False}


def test_timeline_entries():
    ch = [dict(sha="abc1234", date="2026-11-10", subject="s", files=[{"path": "CLAUDE.md"}])]
    e = L.rule_feed(ch, "o/r")[0]
    assert e["type"] == "rule change" and e["mod"] == "rules" and e["text"] == "s (CLAUDE.md)" and e["links"][0][1] == "https://github.com/o/r/commit/abc1234"
