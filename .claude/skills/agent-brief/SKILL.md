---
name: agent-brief
description: "Write a brief for a background agent (research, review or coding). Use before launching any such agent."
---

# Agent brief

Every requirement is the owner's: his own words, or something he approved. A requirement he did not state is proposed to him first and enters the brief only after he approves it.

Sections, in this order:

- **Goal**: the owner's words.
- **Requirements**: one per line, each ending in a tag naming the feedback-log row: `[owner F-123]` (his words) or `[approved F-123]` (he approved it). Nothing untagged.
- **Access Restrictions**: what the agent may and may not read.

Before launching, run `python3 tools/check_brief.py --log <the project's feedback log> BRIEF.md`; launch only if it passes.
