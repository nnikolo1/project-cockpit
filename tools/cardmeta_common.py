"""Shared helpers of the card-metadata collectors (docs/contracts/card-metadata.md, schema 1).

Every collector writes one JSON file per kind into an output folder given by --out-dir (default
~/.local/share/project-cockpit/<project>/cardmeta/, never inside the repository: generated data is not committed).
Pure helpers except `write_kind` (the single file write: temporary name, then rename).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("cardmeta.common")

SCHEMA = 1
KINDS = ("footprint", "churn", "ambiguity")
DATA_DIR = "~/.local/share/project-cockpit"


def iso_utc(now: datetime | None = None) -> str:
    """ISO 8601 UTC with a trailing Z and no microseconds, as in the contract."""
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def header(kind: str, project: str, now: datetime | None = None, until: str | None = None) -> dict:
    """The common header of every output file."""
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}")
    h = {"schema": SCHEMA, "kind": kind, "project": project, "generated_at": iso_utc(now)}
    if until:
        h["until"] = until
    return h


def default_out_dir(project: str) -> str:
    return f"{DATA_DIR}/{project}/cardmeta"


def is_inside(path: Path, repo: Path) -> bool:
    p, r = path.expanduser().resolve(), repo.expanduser().resolve()
    return p == r or r in p.parents


def resolve_out_dir(arg: str | None, repo: Path, project: str) -> Path:
    """Expand --out-dir; refuse a folder inside the repository."""
    out = Path(arg or default_out_dir(project)).expanduser()
    if is_inside(out, repo):
        raise SystemExit(f"--out-dir {out} is inside the repository {repo}: generated data must not be committed")
    return out


def write_kind(out_dir: Path, kind: str, payload: dict) -> Path:
    """Write <out_dir>/<kind>.json atomically (temporary file, then rename)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / f"{kind}.json"
    tmp = out_dir / f".{kind}.json.tmp"
    tmp.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, p)
    log.info("written: %s (%d bytes)", p, p.stat().st_size)
    return p
