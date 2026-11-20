"""Render the launchd plist from the template and the local config (it only prints or writes a file; it never loads or installs anything).

    python3 -m bootfill.render_plist --config CONFIG.json --data-dir DIR --checkout COCKPIT_DIR [--out FILE]

The interval is the config key `poll_minutes` (default 10). The recommended cockpit flag for the budget panel's stale limit is printed to stderr."""
import argparse
import json
import pathlib
import sys
from xml.sax.saxutils import escape

from . import core

TEMPLATE = pathlib.Path(__file__).with_name("com.project-cockpit.bootfill.plist.template")


def render(template, config_path, data_dir, checkout, poll_minutes):
    """Pure: template text + values -> plist text. poll_minutes must be a whole number of at least 1."""
    n = int(poll_minutes)
    if n < 1:
        raise ValueError("poll_minutes must be at least 1")
    out = template.replace("POLL_SECONDS", str(n * 60))
    for key, val in (("CONFIG_PATH", config_path), ("DATA_DIR", data_dir), ("COCKPIT_CHECKOUT", checkout)):
        out = out.replace(key, escape(str(val)))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--config", required=True)
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--checkout", required=True, help="the cockpit checkout (the job's working directory)")
    ap.add_argument("--out", help="write here instead of stdout")
    a = ap.parse_args(argv)
    try:
        over = json.loads(pathlib.Path(a.config).expanduser().read_text())
    except (OSError, ValueError) as ex:
        sys.exit(f"cannot read the config: {ex}")
    cfg = core.merge_config(core.DEFAULTS, over)
    text = render(TEMPLATE.read_text(), str(pathlib.Path(a.config).expanduser().resolve()), str(pathlib.Path(a.data_dir).expanduser().resolve()),
                  str(pathlib.Path(a.checkout).expanduser().resolve()), cfg["poll_minutes"])
    if a.out:
        pathlib.Path(a.out).expanduser().write_text(text)
    else:
        sys.stdout.write(text)
    print(f"interval {cfg['poll_minutes']} min; start the cockpit with --quota-stale-min {core.stale_minutes(cfg['poll_minutes'])}", file=sys.stderr)


if __name__ == "__main__":
    main()
