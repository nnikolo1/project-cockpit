"""API group `settings`: the Dispatcher settings and the module-card options (read, validate, save)."""
import logging
import json

from ckp.core import settings as core_settings
from ckp.store import io as store_io
from dispatcher.core import DEFAULTS as SETTING_DEFAULTS
from dispatcher.core import load_settings
from dispatcher.runtime import Config as RuntimeConfig
from ckp.api import context as api_context

from ckp.api.result import ok
log = logging.getLogger("cockpit")


def get_settings(p):
    """Current values (the defaults when the file is missing) with the cap on workers and the page's minimums."""
    f = api_context.settings_path(p)
    return dict(values=load_settings(f) if f.exists() else dict(SETTING_DEFAULTS), defaults=SETTING_DEFAULTS, minimums=core_settings.SETTING_MINIMUMS, maximums=core_settings.SETTING_MAXIMUMS,
                workers_cap=RuntimeConfig.image_workers, steering=api_context.CFG["steer"])


def get_card_settings(p):
    """Current module-card options; the defaults when the file is missing or unreadable."""
    values = dict(core_settings.CARD_SETTING_DEFAULTS)
    try:
        values = core_settings.validate_card_settings(json.loads(api_context.card_settings_path(p).read_text()), strict=False)
    except FileNotFoundError:
        pass
    except (OSError, ValueError, AttributeError) as ex:
        log.warning("card settings of %s unreadable, using defaults: %s", p.get("name", ""), ex)
    return dict(values=values, defaults=core_settings.CARD_SETTING_DEFAULTS, choices=dict(churn_colour=list(core_settings.CARD_CHURN_COLOURS)), steering=api_context.CFG["steer"])


def save_card_settings(p, form):
    """Validate and write the module-card options atomically, mode 0600. ValueError on a refused value."""
    values = core_settings.validate_card_settings(form)
    f = api_context.card_settings_path(p)
    store_io.atomic_write_json(f, values, mode=0o600, indent=2)
    log.info("card settings saved for %s: %s", p.get("name", ""), values)
    return "card settings saved"


def save_settings(p, form):
    """Validate and write the settings file atomically, mode 0600. ValueError on a refused value."""
    values = core_settings.validate_settings(form, SETTING_DEFAULTS, RuntimeConfig.image_workers)
    f = api_context.settings_path(p)
    try:  # the list of served projects (spec 3c) is not on the form: kept as the file has it
        old = json.loads(api_context._read_text(f) or "{}")
    except ValueError:
        old = {}
    if isinstance(old, dict) and "projects" in old:
        values = dict(values, projects=old["projects"])
    store_io.atomic_write_json(f, values, mode=0o600, indent=2)
    log.info("dispatcher settings saved for %s: %s", p.get("name", ""), values)
    return "settings saved"


# ---------- API entry points (Result) ----------
def settings(ctx):
    """GET /api/settings: the Dispatcher settings with the page's limits."""
    return ok(get_settings(ctx.p))


def cardsettings(ctx):
    """GET /api/cardsettings: the module-card options."""
    return ok(get_card_settings(ctx.p))
