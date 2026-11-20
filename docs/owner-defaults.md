# Owner defaults for all projects

Preferences the owner wants as defaults for every project the cockpit shows. When the owner says "this applies to both projects" (or to all projects), the preference lands here in generic wording, in the project's own instruction file, and, where it concerns assistant behaviour, in the owner's user-level instructions. A new project starts from this list; a project may override an item and says so in its own documentation.

## Questions and tasks
- Every open question for the owner is an issue (labels `type:decision` and `question`, an embryo, with the question, the options, a recommendation and the provenance block) in the repository it belongs to; the cockpit lists them with their age; the answer is a comment, the decision is logged, the issue is closed with a link to the record.
- A request for a metadata metric about code or persistence becomes two tasks: one in the cockpit (default preferences, data contract, display) and one in the project (the collector). Both are embryos; the project task is on the project board and links to its counterpart.
- Only the owner releases tasks; an assistant releases or reprioritises only on an explicit request naming the task, unless the owner has approved a standing delegation with stated limits (see the boot-filling job).

## Module cards (display defaults)
- Persistence footprint per module and sub-module: tables (rows, owner), files and folders (inside and outside the repository, personal-data marker), logging (loggers, files, rotation, what is never logged), statistics (records, sizes, input volume per day, week and month), backup and retention policy.
- Code-change metrics for the last 30 and 90 days: commits, files, lines added and removed, net, churn, source/tests/docs split, active days, trend against the previous window; parents show their own files and the total; colour by relative rank of churn (hot to cold, neutral for no change), never colour alone, with an option to colour relative to module size.
- Assignment ambiguity: files per sub-module, the module's unsorted files with candidates and reasons, a marker when many files are unsorted, the files that are ambiguous between modules on the overview.
- The files that are ambiguous between sub-modules of a module are listed as notes on that module's card (all of them, each with its candidate sub-modules and the reason), because by the module-map rule they stay in the module itself.

## Module map rules
- One owner per file; a file used by several modules goes to its main user and is shown in every module that uses it, marked as shared.
- A file that cannot be assigned unambiguously to a sub-module belongs to the top-level module (its unsorted part). A file that cannot be assigned unambiguously to a module stays explicitly unassigned with its candidate modules and is reported to the owner.
- A memo or ADR clearly about one module or sub-module stays with it; generic ones (several modules, or the project itself) have a generic home and are listed under every module they name.
- Documents are assigned through a `Modules:` line; superseded documents move to an obsolete-documentation folder with a banner naming the replacement; decision records are never moved.

## Data and change discipline
- Commit what a person decides; generate what a script derives, into a local folder, never into git; cards show the age of generated data.
- A change to a database schema needs the owner's explicit approval of the exact statement, one approval per change; ordinary data and code changes do not.
- Anything written into a repository is sanitised first (no secrets, personal data, names of private individuals, home paths) and the owner's phrases are corrected for spelling and grammar before they are written; a public repository additionally names no consuming project.
- A short-cut taken to reach a first version gets a dated note: what, why, limits, first step to do it properly.

## Standing automation (when approved by the owner)
- Boot-filling job: a daemon that polls every 30 minutes, checks that the assistant is idle and the quota windows, and releases a fixed number of ranked tasks near the end of a window (20 tasks when more than 50 percent of the 5-hour quota is left with under 2 hours to its reset; 50 tasks when more than 20 percent of the weekly quota is left with a day or less to its reset). Ranking: priority, deadline, blockers, what a task unblocks, then a recorded judgement of value. Never released automatically: decisions and questions, anything that sends or withdraws something outside the project, schema changes, deletions, spending. Caps, a kill switch and a dry-run week come first. Every poll and every release is logged: a rotated log, an append-only decision log with the ranking inputs and the value judgement, a trail comment on each released task, and the decision log shown on the budget view; skips and failures are logged too.
