"""The install workflow cannot drift from the code (PC-97).

Generalized over every command in the README's Install section and docs/deployment.md's Installation workflow:
a `python3 SCRIPT` or `python3 -m MODULE` command, or a repository script such as `kit/setup.sh`, must exist and
accept every `--flag` the text gives it; a command that does not exist yet must sit on a line marked planned, and a
command marked "(planned: ...)" right after it must still be missing, so the text is updated when it lands.
External tools (gh, git, launchctl, container) are not this repository's and are not checked.
"""
import importlib.util
import pathlib
import re
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SECTIONS = [("README.md", "## Install\n"), ("docs/deployment.md", "## Installation workflow\n")]


def section(text, head):
    """The text from the heading to the next heading of the same level."""
    start = text.index(head)
    level = head.split(" ")[0] + " "
    end = text.find("\n" + level, start + len(head))
    return text[start:] if end < 0 else text[start:end]


def commands(text):
    """(line, command, planned_marker_follows) for every inline code span and indented code line of the text."""
    out = []
    for line in text.splitlines():
        if line.startswith("    ") and not line.strip().startswith(("<", "`")):
            out.append((line, line.strip(), False))
            continue
        for m in re.finditer(r"`([^`]+)`", line):
            out.append((line, m.group(1), bool(re.match(r"\s*\(planned", line[m.end():], re.I))))
    return out


def target(cmd):
    """(kind, name, argv for --help) of a repository command, or None for anything else."""
    tok = cmd.split()
    if tok and tok[0] == "python3" and len(tok) > 2 and tok[1] == "-m":
        rest = tok[3:]
        sub = [t for t in rest[:1] if re.fullmatch(r"[a-z_]+", t)]
        return "module", tok[2], [sys.executable, "-m", tok[2], *sub, "--help"]
    if tok and tok[0] == "python3" and len(tok) > 1 and tok[1].endswith(".py"):
        sub = [t for t in tok[2:3] if re.fullmatch(r"[a-z_]+", t)]
        return "file", tok[1], [sys.executable, tok[1], *sub, "--help"]
    if tok and re.fullmatch(r"[\w.-]+(/[\w.-]+)+\.(sh|py)", tok[0]):
        return "file", tok[0], None
    return None


def exists(kind, name):
    if kind == "module":
        return importlib.util.find_spec(name) is not None
    return (ROOT / name).is_file()


CASES = [(f, line, cmd, planned) for f, head in SECTIONS for line, cmd, planned in commands(section((ROOT / f).read_text(), head))
         if target(cmd)]


def test_sections_have_commands():
    for f, _ in SECTIONS:
        assert any(c[0] == f for c in CASES), f"{f}: no repository command found in the install section"


@pytest.mark.parametrize("f,line,cmd,planned", CASES, ids=[f"{c[0]}:{c[2]}" for c in CASES])
def test_command_exists_or_is_planned(f, line, cmd, planned, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT))
    kind, name, helpargv = target(cmd)
    present = exists(kind, name)
    if planned:
        assert not present, f"{f}: `{cmd}` is marked planned but {name} exists: describe it as working"
        return
    if not present:
        assert "planned" in line.lower(), f"{f}: `{cmd}`: {name} does not exist and the line does not mark it planned"
        return
    flags = re.findall(r"(?<!\S)(--[a-z][\w-]*)", cmd)
    if helpargv and flags:
        r = subprocess.run(helpargv, cwd=ROOT, capture_output=True, text=True, timeout=60,
                           env={"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin"})
        assert r.returncode == 0, f"{f}: `{' '.join(helpargv[1:])}` failed: {r.stderr[-300:]}"
        for flag in flags:
            assert flag in r.stdout, f"{f}: `{cmd}`: {name} has no option {flag}"


def test_planned_steps_name_a_task_or_say_none():
    """Wrinkle rule: a planned step names its task (PC-nn) or says plainly that it has none yet."""
    for f, head in SECTIONS:
        for line in section((ROOT / f).read_text(), head).splitlines():
            for m in re.finditer(r"planned[^.;)]*", line, re.I):
                assert re.search(r"PC-(\d+|nn)|no task yet", m.group(0)), f"{f}: planned step without a task: {line.strip()[:120]}"
