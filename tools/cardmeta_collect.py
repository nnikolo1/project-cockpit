#!/usr/bin/env python3
"""Run the three card-metadata collectors (footprint, churn, ambiguity) into ONE output folder.

A failing collector is logged and does not stop the others; the exit code is the worst one.
Usage: python3 tools/cardmeta_collect.py --project NAME [--repo .] [--map FILE] [--declared FILE] [--out-dir DIR] [--days 30 --days 90] [--only churn]
Default output folder: ~/.local/share/project-cockpit/<project>/cardmeta/ (never inside the repository).
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cardmeta_ambiguity as amb  # noqa: E402
import cardmeta_churn  # noqa: E402
import cardmeta_common as cm  # noqa: E402
import cardmeta_footprint  # noqa: E402

log = logging.getLogger("cardmeta.collect")


def plan(a: argparse.Namespace, repo: Path, project: str, out_dir: Path) -> list[tuple[str, object, list[str]]]:
    """Pure: (name, main function, argv) of every collector to run, in order."""
    common = ["--repo", str(repo), "--project", project, "--out-dir", str(out_dir)] + (["--map", a.map] if a.map else [])
    steps = [("footprint", cardmeta_footprint.main, common + (["--declared", a.declared] if a.declared else [])),
             ("churn", cardmeta_churn.main, common + [x for d in (a.days or []) for x in ("--days", str(d))]),
             ("ambiguity", amb.main, common)]
    return [s for s in steps if not a.only or s[0] in a.only]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    amb.add_common_args(ap)
    ap.add_argument("--declared")
    ap.add_argument("--days", type=int, action="append")
    ap.add_argument("--only", action="append", choices=cm.KINDS)
    a = ap.parse_args(argv)
    repo, _map, project = amb.setup(a)
    out_dir = cm.resolve_out_dir(a.out_dir, repo, project)
    worst = 0
    for name, fn, args in plan(a, repo, project, out_dir):
        t0 = time.monotonic()
        try:
            rc = fn(args) or 0
        except SystemExit as e:
            rc = e.code if isinstance(e.code, int) else 2
        except Exception:
            log.exception("collector %s crashed", name)
            rc = 3
        log.info("collector %s finished with exit code %s in %.1fs", name, rc, time.monotonic() - t0)
        worst = max(worst, rc)
    log.info("output folder: %s", out_dir)
    return worst


if __name__ == "__main__":
    sys.exit(main())
