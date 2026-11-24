# 0004. Read-mostly app, opt-in steering, local by default

Status: accepted; the default of steering amended 2026-11-24 (see Amendment)
Date: 2026-11-17

## Context
The cockpit is mainly a viewer. A viewer that can silently change a project, or that is reachable by anyone on the network, is a risk out of proportion to its job, and it holds the user's GitHub authority (it acts as the `gh` login).

## Decision
- Every steering action (release, change, cancel, block, file a task, set a module deadline, accept a proposal) is one or a few real GitHub operations, planned by pure code and then executed with `gh`. Nothing is kept locally.
- A read-only guard: `do_act` refuses every action with HTTP 403 unless the app was started with `--steer`. With `--steer` it warns at start if the `gh` login is not the Manager (labels set then would be flagged).
- Access needs a random token, given once in the URL (`?t=`), then kept in an HttpOnly, SameSite=Strict cookie; the request log hides the token. `--token-file` keeps it across restarts (file mode 0600).
- The server listens on 127.0.0.1. `--lan` binds all interfaces and warns that plain HTTP sends the token unencrypted.

## Consequences
- Safe by default: starting the app can change nothing.
- Anything the page does can be done, and audited, in GitHub itself.
- Given up: multi-user access with separate rights (there is one token), encryption on the network, and any action that is not a GitHub operation.

Revisit when: the cockpit must serve several people, or run anywhere other than a trusted machine or network.

## Amendment (2026-11-24, owner)

Steering is the default: the cockpit acts unless started with `--read-only`. `--steer` is still accepted and does nothing, so that existing service files keep starting. The token, the loopback default and the warning when the `gh` login is not the Manager are unchanged.
