"""Write the explicit decision to work an already-worked task again (ADR 0019).
Usage: python3 -m bootfill.requeue --data-dir DIR --project NAME --number N --by orchestrator|manager --reason "why"
The job never redoes a task by itself; the next poll starts one worker for the task after this record. Phase 1 has no identity separation,
so `--by` is a statement of who decided, not an authentication."""
import argparse
import time

from . import adapters as A, core


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--number", required=True, type=int)
    ap.add_argument("--by", required=True, choices=["orchestrator", "manager"])
    ap.add_argument("--reason", required=True)
    a = ap.parse_args(argv)
    rec = core.requeue_record(time.time(), a.project, a.number, a.by, a.reason)
    A.append_jsonl(f"{a.data_dir}/decision-log.jsonl", rec)
    print(f"requeue recorded for {a.project} #{a.number} by {a.by}")


if __name__ == "__main__":
    main()
