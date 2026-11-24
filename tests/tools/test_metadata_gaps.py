"""tools/metadata_gaps.py: exit code and JSON shape as documented in docs/contracts/metadata-gaps.md."""
import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "tools"))
import metadata_gaps as G  # noqa: E402

NOW = dt.datetime(2026, 11, 24, 12, 0, tzinfo=dt.timezone.utc)
STAMP = "2026-11-24T10:00:00Z"


def setup(tmp_path, with_data):
    """A projects file for project `demo` with a one-module map; with_data writes the card files and token files."""
    (tmp_path / "m.json").write_text(json.dumps({"modules": [{"id": "a", "name": "A", "desc": "x"}], "assign": []}))
    (tmp_path / "projects.json").write_text(json.dumps({"projects": [{"name": "demo", "repo": "o/demo", "modules": str(tmp_path / "m.json")}]}))
    if with_data:
        meta = tmp_path / "demo" / "cardmeta"
        meta.mkdir(parents=True)
        for k in ("footprint", "churn", "ambiguity"):
            (meta / f"{k}.json").write_text(json.dumps({"schema": 1, "kind": k, "project": "demo", "generated_at": STAMP, "modules": {"a": {"policy": {"backup": "git", "retention": "kept"}}}}))
        for f in ("quota-history.jsonl", "transcript-usage.jsonl", "proxy-usage.jsonl"):
            (tmp_path / "demo" / f).write_text(json.dumps({"ts": STAMP}) + "\n")


def run(tmp_path, capsys, *extra):
    code = G.main(["--projects-file", str(tmp_path / "projects.json"), "--data-dir", str(tmp_path), *extra], now=NOW)
    return code, capsys.readouterr().out


def test_gaps_give_exit_1_and_the_documented_json(tmp_path, capsys):
    setup(tmp_path, with_data=False)
    code, out = run(tmp_path, capsys, "--json")
    rep = json.loads(out)
    assert code == 1
    assert set(rep) == {"schema", "project", "generated_at", "max_age_hours", "count", "gaps"}
    assert rep["schema"] == 1 and rep["project"] == "demo" and rep["max_age_hours"] == 24 and rep["count"] == len(rep["gaps"]) > 0
    assert all(set(g) == {"project", "module", "kind", "what", "age_h"} for g in rep["gaps"])


def test_text_report_has_one_line_per_gap(tmp_path, capsys):
    setup(tmp_path, with_data=False)
    code, out = run(tmp_path, capsys)
    assert code == 1 and out.splitlines()[0].startswith("demo | ") and out.splitlines()[-1].endswith("gap(s)")


def test_unknown_project_or_missing_file_gives_exit_2(tmp_path, capsys):
    setup(tmp_path, with_data=False)
    assert run(tmp_path, capsys, "--project", "nope")[0] == 2
    assert G.main(["--projects-file", str(tmp_path / "none.json")], now=NOW) == 2


def test_card_and_token_data_present_leaves_only_the_unconfigured_parts(tmp_path, capsys):
    """With card and token data present, no card_data, token_usage or stale gap is left (no `code`, no rules, no clone in this setup)."""
    setup(tmp_path, with_data=True)
    code, out = run(tmp_path, capsys, "--json")
    ks = {g["kind"] for g in json.loads(out)["gaps"]}
    assert code == 1 and not ks & {"card_data", "token_usage", "stale"}


def test_timing_check_against_a_local_server(tmp_path):
    import http.server, threading
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(404 if "/api/code" in self.path else 200)
            self.end_headers()
            self.wfile.write(b"{}")
        def log_message(self, *a):
            pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        t = dict(G.time_requests(f"http://127.0.0.1:{srv.server_port}/?t=x", "demo", 3))
    finally:
        srv.shutdown()
    assert t["code"] is None and t["state"] < 3 and set(t) == set(G.TIMED)
