# 0011. Deployment: role separation by identity and credentials (one cockpit per repo, since extended by ADR 0012)

Status: accepted
Date: 2026-11-17

## Context
The constitution wants the write rules of the Manager, Orchestrator and Workers enforced by identity and access control, not by instruction. GitHub gives no per-label permission (0003), the cockpit stores nothing (0001) and acts as the `gh` login (0004).

## Decision
- One cockpit process per repository, run under the Manager's OS user, on loopback by default, as a user-level service (LaunchAgent or systemd user unit). Orchestrator and Workers never hold the Manager's credential nor run as that user.
- Separation is built in three phases: 1 one identity (nothing enforced), 2 a separate Orchestrator identity with a ruleset, CODEOWNERS and the policy workflow, 3 separate Worker identities in separate OS users or containers.
- The docs state, per layer, whether it is enforced by GitHub, by the OS, only after the fact, or only by convention. Each phase has a forgery test as its acceptance check.
- The `--steer` guard stays a warning when the `gh` login is not the Manager; the login is the control, not the flag.

## Consequences
- The Manager's rights are the only ones that matter, so the design protects one credential.
- Labels stay open to every role that can label; protection is detection, ignoring and revert (0003).
- Anyone with Write can change the `MANAGER` variable; the cockpit's own `--manager` still flags forgeries.
- Given up: prevention of label changes, multi-user access to the cockpit, encryption on the LAN.

Revisit when: GitHub offers per-label permissions, or the cockpit serves several people.

Details and recipes: [deployment](../deployment.md).
