"""GitHub adapter of the deploy command (PC-84): releases and deployments through the `gh` command line."""
import hashlib
import json
import tempfile
import logging
from pathlib import Path

from ckp.adapters import run as _run

log = logging.getLogger("ckp.adapters.github")


def create_release(repo, tag, notes, asset=None, *, run=_run):
    """`gh release create`; `asset` (a tarball) is uploaded with a `<name>.sha256` file next to it. Returns the release URL."""
    args = ["gh", "release", "create", tag, "--repo", repo, "--title", tag, "--notes", notes]
    if asset is not None:
        asset = Path(asset)
        sum_file = asset.with_name(asset.name + ".sha256")
        sum_file.write_text(f"{hashlib.sha256(asset.read_bytes()).hexdigest()}  {asset.name}\n")
        args += [str(asset), str(sum_file)]
    url = run(args).strip()
    log.info("release %s created in %s: %s", tag, repo, url)
    return url


def record_deployment(repo, tag, environment, state, description, *, run=_run):
    """Create a GitHub deployment of `tag` to `environment` and set its status (`state`: success, failure, ...)."""
    body = dict(ref=tag, environment=environment, auto_merge=False, required_contexts=[], description=description[:140])
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=True) as fh:
        json.dump(body, fh)
        fh.flush()
        out = run(["gh", "api", f"repos/{repo}/deployments", "-X", "POST", "--input", fh.name])
    dep_id = json.loads(out)["id"]
    run(["gh", "api", f"repos/{repo}/deployments/{dep_id}/statuses", "-X", "POST", "-f", f"state={state}",
         "-f", f"description={description[:140]}"])
    log.info("deployment %s of %s to %s recorded as %s", dep_id, tag, environment, state)


def latest_deployment(repo, environment, *, run=_run):
    """The newest deployment of the environment as `{id, ref, environment, state, description, created_at}`, or None."""
    deps = json.loads(run(["gh", "api", f"repos/{repo}/deployments?environment={environment}&per_page=1"]))
    if not deps:
        return None
    d = deps[0]
    sts = json.loads(run(["gh", "api", f"repos/{repo}/deployments/{d['id']}/statuses?per_page=1"]))
    state = sts[0]["state"] if sts else None
    return {"id": d["id"], "ref": d["ref"], "environment": d.get("environment", environment), "state": state,
            "description": (sts[0].get("description") if sts else d.get("description")), "created_at": d.get("created_at")}
