# Working rules

Project cockpit is a light-weight, opinionated viewer over GitHub for agent-assisted projects. It is NOT another project management system.

- Principles: Purity, Frugality, Brevity. Every line must be required by `docs/constitution.md` or the user's words; prefer deleting to generalising.
- Stdlib only. No token storage: the app acts as the `gh` login.
- Read-only unless `--steer`.
- Tests: pytest, pure logic, no network. Logging via `logging`, not print. Explain every shell command.
- No data or names of any consuming project in this repo.
