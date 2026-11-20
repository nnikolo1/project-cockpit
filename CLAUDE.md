# Working rules

Project cockpit is a light-weight, opinionated viewer over GitHub for agent-assisted projects. It is NOT another project management system.

- Principles: Purity, Frugality, Brevity. Every line must be required by `docs/constitution.md` or the user's words; prefer deleting to generalising.
- Stdlib only. No token storage: the app acts as the `gh` login.
- Read-only unless `--steer`.
- Tests: pytest, pure logic, no network. Logging via `logging`, not print. Explain every shell command.
- No data or names of any consuming project in this repo.
- Open questions for the owner (standing rule, owner, 2026-11-20): every question an assistant has for the owner that is still open is persisted as a GitHub issue in the repository it belongs to (labels `type:decision` and `question`, an embryo, with the question, the options, a recommendation and the provenance block) and is listed by the cockpit for every project it shows (label `question`, open, with its age). An answer is recorded as a comment, the decision is logged, and the issue is closed with a link to that record.
- Documents (owner, 2026-11-20): a memo or ADR that is clearly about one module or sub-module stays with it; generic ones (several modules, or the project itself) get their own generic home and are listed under every module they name, never moved.
- Owner defaults for all projects (owner, 2026-11-20): the list of preferences the owner wants as defaults for every project lives in docs/owner-defaults.md; a new project starts from it.
- Module cards (owner, 2026-11-20): the files that are ambiguous between sub-modules of a module are listed as notes on that module's card, all of them, each with candidates and reason (data: `unsorted` entries with `candidates` in the ambiguity file of the card-metadata contract).
