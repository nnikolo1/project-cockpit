# 0022. Policy packs stored where Claude reads them; the module map is a setting

Status: accepted in direction (owner, 2026-11-25); details open
Date: 2026-11-25
Modules: internal/agents, devops/policy

## Context
The Cockpit is the owner's template for future projects, and the owner wants it to be of interest to other developers, so its idiosyncrasy must stay within limits (F-936). Two kinds of data end up in the repositories of the projects it serves: the module map (a post-hoc aid for a codebase that grew without planned modules) and policy defaults (the owner's personal preferences, consumed by Claude).

## Decision
1. **Module map: a setting of the local installation.** Whether a project uses a module map is a setting in the Cockpit's own settings (per project, default: modules inferred from the directory layout; alternatives: use the map file, none); the default can be overridden. The map file, when used, is committed in the project (`.cockpit/modules.json`) because a person decides it. The ambiguity and unsorted-share checks run only where a map is used.
2. **Policies are stored where their consumer reads them.** Policies are meant to be consumed by Claude, so they live in Claude's instruction files, in three packs:
   - **core**: the few invariants the Cockpit's mechanism needs (tasks as issues, the meaning of the released label, blocked tasks not assigned); part of the Cockpit's code and constitution, not copied into projects.
   - **base**: a short set of universal rules; delivered into the project's instruction file as a managed block (opt-in at install).
   - **owner**: the owner's personal preferences; stored in the owner's user-level instruction file and skills (which Claude loads for every project of the owner), never injected into another project's repository; for Workers it is delivered through the Worker image's rules block.
   A project may add its own rules outside the managed block.
3. The policy registry (PC-91) gives each policy its pack; the compliance check (PC-108) tests only the packs a project has enabled; the owner's user-level file is read-only to every check (human in the loop).

## Consequences
The constitution states which of its rules are core, base or owner (PC-33 sorts them). PC-91, PC-92 (the manifest declares the packs and the module setting) and PC-108 follow. Nothing of the owner pack enters a repository of another project.
