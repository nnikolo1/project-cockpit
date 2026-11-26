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


def asset_name(tag):
    """The release asset of a tag: the archive of the tagged tree (a `.sha256` file with the same name next to it)."""
    return f"{tag}.tar.gz"


def download_release(repo, tag, dest, *, run=_run):
    """Download the release asset of `tag`, check it against its `.sha256` file and unpack it into `dest`/tree; returns that folder.
    A missing asset or a wrong checksum raises (nothing is unpacked)."""
    import tarfile
    dest, name = Path(dest), asset_name(tag)
    dest.mkdir(parents=True, exist_ok=True)
    run(["gh", "release", "download", tag, "--repo", repo, "--pattern", name, "--pattern", name + ".sha256", "--dir", str(dest), "--clobber"])
    arch = dest / name
    want = (dest / (name + ".sha256")).read_text().split()[0]
    got = hashlib.sha256(arch.read_bytes()).hexdigest()
    if got != want:
        raise ValueError(f"release asset {name} of {repo}: checksum {got} is not the published {want}")
    tree = dest / "tree"
    with tarfile.open(arch) as t:
        t.extractall(tree, filter="data")
    log.info("release asset %s of %s unpacked into %s", name, repo, tree)
    return tree


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


def gh_of(run=_run):
    """The `gh(args) -> stdout` function dispatcher.tasks takes, over the injected runner (args without the leading `gh`)."""
    return lambda args: run(["gh", *args])


def create_issue(repo, title, body, labels=(), *, run=_run):
    """`gh issue create`; returns the new issue's URL. Labels must exist in the repository."""
    args = ["gh", "issue", "create", "--repo", repo, "--title", title, "--body", body]
    for label in labels:
        args += ["--label", label]
    url = run(args).strip()
    log.info("issue created in %s: %s", repo, url)
    return url


def comment_issue(repo, number, text, *, run=_run):
    """`gh issue comment`: add one comment to issue `number`."""
    run(["gh", "issue", "comment", str(number), "--repo", repo, "--body", text])
    log.info("comment added to %s#%s", repo, number)
