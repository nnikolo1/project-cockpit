"""The Workers' Stop hook (spec docs/specs/dispatcher-and-workers.md, 4t). Standard library only, plus note.py
in the same folder. Claude Code passes a JSON object on stdin; exit code 2 refuses the stop and stderr is
shown to the Worker. `stop_hook_active` is true when the Worker is already continuing after one refusal, so a
second failure lets it end (the Dispatcher then fails the task by 3e).
Environment (set by Runtime.launch): WORKER_TASK_ID, WORKER_OUTBOX. The hook runs in the Worker's clone."""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:  # in the image note.py sits next to this file; in the repository it is dispatcher/note.py
    import note as good_bye  # noqa: E402
except ImportError:
    from dispatcher import note as good_bye  # noqa: E402


def commits_beyond_main(clone, task_id):
    """Commits on worker/<task-id> that origin/main does not have; 0 when the branch or main is missing."""
    try:
        out = subprocess.run(["git", "-C", clone, "rev-list", "--count", f"origin/main..worker/{task_id}"],
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return 0
    return int(out.stdout.strip()) if out.returncode == 0 and out.stdout.strip().isdigit() else 0


def problems(clone, task_id, outbox):
    """What is missing before the Worker may end; an empty list means it may."""
    path = os.path.join(outbox, "goodbye.json")
    try:
        with open(path, encoding="utf-8") as fh:
            parsed = json.load(fh)
    except FileNotFoundError:
        return [f"{path} is missing: write the good-bye note"]
    except (OSError, ValueError) as exc:
        return [f"{path} is not readable JSON ({exc})"]
    found = [f"goodbye.json: {p}" for p in good_bye.validate(parsed)]
    if isinstance(parsed, dict) and parsed.get("outcome") == "gave_up":
        return found
    if commits_beyond_main(clone, task_id) < 1:
        found.append(f"branch worker/{task_id} has no commit beyond main: commit your work on it")
    if not os.path.isfile(os.path.join(outbox, "result.bundle")):
        found.append(f"{outbox}/result.bundle is missing: write a git bundle of worker/{task_id}")
    return found


def main(stdin=None, env=None):
    env = os.environ if env is None else env
    try:
        event = json.load(stdin or sys.stdin)
    except ValueError:
        event = {}
    if event.get("stop_hook_active"):
        return 0  # refused once already: let the Worker end
    task_id, outbox = env.get("WORKER_TASK_ID"), env.get("WORKER_OUTBOX")
    if not task_id or not outbox:
        return 0  # not a Dispatcher Worker run
    found = problems(event.get("cwd") or os.getcwd(), task_id, outbox)
    if found:
        print("You cannot stop yet:\n- " + "\n- ".join(found), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
