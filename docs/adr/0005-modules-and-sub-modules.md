# 0005. Modules and sub-modules are configured, not created in the app

Status: accepted
Date: 2026-11-17

## Context
Tasks need grouping (areas of the product), deadlines per group, and a mapping from code paths to groups. A module hierarchy deeper than needed would complicate the page and the rules.

## Decision
- Modules come from a JSON file (`--modules`): `modules` (id, name) and `assign` (path, module). Ids are slash paths: `alpha` is a module, `alpha/one` a sub-module. One nesting level is required; the parent must be listed too.
- A task gets a module from the Project field (top level only) or a `module:<id>` label. The field stays flat; a sub-module label wins only under the same top-level module, otherwise the field wins (`task_module`).
- Code paths are assigned by longest matching prefix, so `alpha/one` beats `alpha` (`map_path`).
- The deadline of a task is the first found along: the task, its parent task, its sub-module, its module (`module_deadline`, stored in the repo variable `MODULE_DEADLINES`).
- Modules are created by editing the file. There is no creation screen yet, deliberately.

## Consequences
- The module structure is reviewable text, versioned with the project.
- Given up: deeper nesting, creating modules from the page, and a second source of truth for module names.
- Each module or path change needs a restart or file edit by whoever owns the config.

Revisit when: adding modules becomes frequent enough to justify a screen (a possible shape: create the `module:<id>` label from the page).
