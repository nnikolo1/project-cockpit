import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import logic as L


def make(tmp_path):
    (tmp_path / "report").mkdir()
    (tmp_path / "report" / "deps-alpha.html").write_text("<p>x</p>")
    (tmp_path / "secret.html").write_text("no")
    return tmp_path


def test_serves_plain_report_files(tmp_path):
    d = make(tmp_path)
    assert L.report_file(d, "deps-alpha.html").name == "deps-alpha.html"
    assert L.report_file(d, "missing.html") is None


def test_rejects_traversal_and_odd_names(tmp_path):
    d = make(tmp_path)
    for bad in ("../secret.html", "a/b.html", "..", ".hidden.html", "x.py", "", None):
        assert L.report_file(d, bad) is None
