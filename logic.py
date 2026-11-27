"""Pure logic of the cockpit: no network, no subprocess. Everything here is unit-tested.

Shim (refactoring slice 1, docs/contracts/ckp-architecture.md): the code lives in the themed modules of ckp/core; this module
re-exports every name it used to define, private ones included, so `import logic as L` keeps working. Deleted only in the final cleanup slice."""
import bisect  # noqa: F401
import datetime as dt  # noqa: F401
import json  # noqa: F401
import logging  # noqa: F401
import math  # noqa: F401
import pathlib  # noqa: F401
import re  # noqa: F401
import shlex  # noqa: F401

from ckp.core.common import (  # noqa: F401
    STATES, STAGES, GAPS_MAX_AGE_H, _age_h,
)
from ckp.core.settings import (  # noqa: F401
    SETTING_MINIMUMS, SETTING_MAXIMUMS, SETTING_LABELS, validate_settings, CARD_CHURN_COLOURS, QUALITY_DEFAULTS, QUOTA_SETTING_DEFAULTS,
    QUEUE_REFRESH_DEFAULT, QUEUE_REFRESH_MIN, QUEUE_BUDGET_RESERVE, CARD_SETTING_DEFAULTS, _is_num, validate_thresholds, validate_card_settings,
    validate_queue_refresh, validate_quota_settings,
)
from ckp.core.modules import (  # noqa: F401
    short_name, SUBID, parent_id, top_id, chain, resolve_module, task_module, with_parents, add_tree, module_deadline, MODID, HLID, SVG_TAGS,
    SVG_ATTRS, SVG_MAX, _SAFE_VALUE, _SAFE_STYLE, module_stem, module_paragraphs, hl_ids, sanitise_svg, group_files, CODE_EXT, file_kind,
    biggest_files, module_files, code_lines, snapshot_lines, order_modules, doc_modules, doc_title, module_docs, _py_targets, import_edges,
    module_relations, map_path, _g, code_view, snapshot_age, collect_command, refresh_outcome, CARD_KINDS, STALE_HOURS, UNSORTED_WARN, HOT_SHARE,
    CARD_CHURN_DAYS, GAP_WORDS, _num, parse_card, rank_bands, churn_view, _weeks, changes_view, file_location, policy_text, footprint_view,
    ambiguity_view, CARD_VIEWS, card_meta, BAND_WORDS, band_of, _per_kloc, module_bands, USAGE_FIELDS, map_entry,
)
from ckp.core.rules import (  # noqa: F401
    RS, US, GS, LOG_FMT, BOT, TRAILER, DIFF_CAP, LINE_CAP, glob_re, rules_config, rule_group, rule_pathspecs, is_agent_assisted, is_manager, _records,
    parse_numstat, diff_excerpt, parse_rule_changes, parse_last_changes, rule_files, pending_rule_prs, rule_feed, REPORT_NAME, report_file,
)
from ckp.core.commits import (  # noqa: F401
    commit_lanes, parse_git_full, group_commits, HEAT_LEVELS, shade_level, activity_days, issue_entries, ref_link, module_dir, static_entries,
    filter_feed,
)
from ckp.core.usage import (  # noqa: F401
    WINDOW_SECONDS, _json_lines, _epoch, _counts, _add, HISTORY_DAYS, _pct, _iso, MONTHLY_MONTHS, _period, _periods, _held_peaks, _quota,
    SESSIONS_SHOWN, _message_counts, _period_skeleton, record_kind, transcript_report, heat_days, _history, _add_messages, MONTHS_SHOWN,
    UNATTENDED_KINDS, UNASSIGNED, _months, source_project, monthly_by_project, usage_report, WEEK_SECONDS, QUOTA_KINDS, _utc, vendor_windows,
    estimate_percent, _quota_kind, quota_estimates,
)
from ckp.core.tasks_model import (  # noqa: F401
    PRIO, PROTECTED, DUE, KEY, PROPOSAL, RESOLVED, KIND_TYPE, LINK, is_date, is_protected, split_title, parse_due, parse_proposals,
    parse_module_deadlines, policy, issues_needing_timeline, stage, task_lookup, FINDING, FILED_BY, is_finding, NOT_DISPATCHABLE, INTERACTIVE_DAYS,
    _author_releases, dispatchable, is_blocked, pickable, parse_filed_by, KEPT, ANALYSIS, split_text, join_text, CHECKABLE_ACTIONS, action_applied,
    analysis_state, is_running, swap_priority, _prefixed, derive, event_text, task_events, norm_issue, _flag, _fields, apply_ops, replace_issues,
    norm_events, RECOMMENDATION, RECO_CAP, parse_recommendation, age_days, parse_questions, merge_questions, group_questions, parse_project,
)
from ckp.core.queue_view import (  # noqa: F401
    queue_interval, is_rate_limited, rate_limit_reset, _TOKEN_MARKS, _UNREACHABLE_MARKS, offline_kind, offline_reason, NEW_WORK_MAX_AGE_S,
    may_start_new, stagger_plan, refresh_due, _when, queue_local, capacity, project_key, listed_projects, _due_chain, queue_all, LOG_KINDS,
    LOG_LIMITS, GROUP_LINES, QUIET, READINGS, _GIB, _MIB, _size, container_reading, parse_heartbeat, _row, log_rows, LIVE_STAGE, RECONCILED,
    LIVE_REFRESH_S, task_number, live_events, overlay_live, live_refresh_due, local_modules, OPEN_KINDS, open_kind, open_tip, open_counts,
)
from ckp.core.health import (  # noqa: F401
    QUALITY_METRICS, _gap, _latest_ts, _stale, metadata_gaps, BACKUP_STATUS_FILE, backup_status, backup_status_gaps, CI_WORKFLOW, GATE_LOG, CI_FIELDS,
    CI_RED, RED_MAIN_TITLE, RED_MAIN_POLICY, ci_run, gate_result, ci_latest, red_main_body, RETENTION_MIN_DAYS, transcript_retention_gaps,
    _quality_gaps, _footprint_gaps, _snapshot_gaps, OFFLINE_PANES, backup_gaps, pane_problem, COMPLIANCE_REPORT_FILE, compliance_summary,
    COMPLIANCE_REPORT, parse_compliance, compliance_run_state, ACTION_LOG_LIMIT, _LOG_PARAMS, _LOG_SIZES, _SECRETISH, scrub, _short, _page_id,
    _now_iso, action_record, PAGE_KINDS, page_record, filter_events,
)

log = logging.getLogger("cockpit")
