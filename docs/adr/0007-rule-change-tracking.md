# 0007. Rule-change tracking (the Rules tab)

Status: accepted
Date: 2026-11-17

## Context
In an agent-assisted project, the files that steer the agents (instructions, constitution, policy) are as important as code, and an agent may edit them. The Manager needs to see quickly when they change.

## Decision
- The project declares its constitutional paths as named groups of globs in the `rules` key of the `--modules` file (README has the format), plus the names or e-mails that count as Managers.
- The cockpit reads them from a local git clone (`--git`): the file list with last change, the last 90 days of changes with a diff excerpt, and open pull requests touching them.
- A change is flagged as agent-assisted when it has a `Co-Authored-By` trailer or a bot-like author (`is_agent_assisted`), or when its author is not a listed Manager.
- The review marks ("seen") are kept only in the browser (localStorage); nothing is written to the repo or to a server.

## Consequences
- No agent has to cooperate: the detection reads what git already records.
- The limit: the trailer is a convention, not proof. An agent that omits it, or works under the Manager's own identity, is not flagged; with one identity the tab is only a hint (see 0003).
- Review marks do not follow the user to another browser or machine, and are not a record anyone else can see.
- Needs a clone on the machine that runs the cockpit.

Revisit when: agents have separate identities (flags become reliable), or review marks must be shared.
