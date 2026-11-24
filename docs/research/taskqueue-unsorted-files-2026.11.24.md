# Task Queue: unsorted files, structure analysis (policy finding PC-96)

Status: research report, suggestions for the owner only (structure policy, owner 2026-11-24: the analysis never changes the module map; nothing in `docs/cockpit-modules.json` was edited)
Date: 2026-11-24
Modules: taskqueue, internal/metadata

## Figures

Card data of 2026-11-24: 14 unsorted of 44 (Dispatcher 19, Worker 5, E2E 6), 31.8 percent. Re-run on main `0b9dc63` (`tools/cardmeta_ambiguity.py --format md`): Dispatcher has grown to 21 (`tools/stats_cache_usage.py`, `tools/transcript_usage.py`), so 14 of 46, 30.4 percent: still above the threshold, by one file. The 14 files are the same.

## Per file

| File | Candidates | Why ambiguous | Suggestion |
|---|---|---|---|
| `dispatcher/note.py` | dispatcher, worker | imported by `dispatcher/runtime.py` (Dispatcher reads the note) and by `dispatcher/image/stop_hook.py` (Worker checks it) | **Worker.** Its docstring calls it "the Worker's good-bye note", the Containerfile copies it into the image (`COPY ... dispatcher/note.py /opt/hook/`), and it is stdlib-only because it runs in the container. The Dispatcher's import is an ordinary consumer import across sub-modules, like the Worker's use of the proxy. Confirms the expected suggestion. |
| `tests/dispatcher/test_note.py` | dispatcher, worker | follows `note.py` by the mirror rule | **Worker**, automatically once `note.py` moves; no entry of its own needed. Confirms the expected suggestion. |
| `tests/dispatcher/test_stop_hook.py` | none recorded | **not a placement question but a resolver defect.** The map already has an entry for this test path with module `taskqueue/worker`. `cardmeta_map.entry_of` tries the mirror candidates first: `dispatcher/stop_hook.py` is not tracked, but it matches the prefix entry `dispatcher/` (taskqueue), so the test's own exact entry is never consulted. The reason shown on the card is therefore the basis of `dispatcher/`, not of the file. | **Worker**, which the map already says. Fix the resolver (generic, `internal/metadata`): an exact entry for the test path beats a mirror match that only hits a directory prefix; or, as before, an alias entry `dispatcher/stop_hook.py`. Confirms the expected suggestion; the cause is new. |
| `dispatcher/__init__.py` | dispatcher, worker | empty package marker (0 bytes) of a package shared by both sides | **Do not count it** (see counting rule). If it must be placed: Dispatcher (the package is named after it and 9 of its 11 Python modules are Dispatcher modules). Confirms the expected suggestion. |
| `docs/specs/dispatcher-and-workers.md` | none | the specification of the whole module (`Modules: taskqueue`) | **Stays with the parent**, as the module's own file: it fits the module itself, not "no sub-module". |
| `docs/adr/0015`, `0016`, `0019` | none | superseded boot-filling ADRs about the job as a whole (implementation choices, authority judgement, released once and worked once) | **Stay with the parent.** |
| `docs/adr/0017`, `0018` | (dispatcher) | superseded ADRs on the quota history, which is `dispatcher/quota.py` today | Could go to **Dispatcher**, but only by changing their `Modules:` line (owner rule on documents, 2026-11-20) on superseded records. Recommended: leave them with the parent. |
| `docs/adr/0020` | (worker) | superseded ADR on worker privileges | Same: could be **Worker** via its `Modules:` line; recommended: leave it. |
| `docs/memos/2026.11.24-refactoring-for-agent-and-os-independence.md` | none | covers the runtime adapter and host probes (Dispatcher) and the image (Worker) | **Stays with the parent**: spans two sub-modules. |
| `docs/research/agent-os-dependence-2026.11.24.md` | none | same span as the memo | **Stays with the parent.** |
| `docs/research/container-seam-review-2026.11.24.md` | none | proxy (Dispatcher) and relay (Worker) | **Stays with the parent.** |

Also: ADR 0019's `Modules:` line names `internal/cockpit`, which is no module of this map (its map basis says so). A one-word fix of the line (`githubclient` or nothing) is the owner's call.

## The counting rule

Today every file that resolves to a module with sub-modules is "unsorted" (`tools/cardmeta_ambiguity.py`, `aggregate`: `own_files` is forced to 0 for a parent). That mixes three different things:

1. files **ambiguous** between sub-modules (they have `candidates`): the only ones the owner's rule ("fit no sub-module unambiguously") clearly means, and the only ones the card lists as notes;
2. files **placed in the parent by a recorded decision** that they are about the whole module, chiefly documents whose `Modules:` line names the parent (owner rule on documents, 2026-11-20): they fit the module, not "no sub-module";
3. **package markers** (`__init__.py`, here empty): structure, not content.

Shares under each reading:

| Reading | Card data (44) | Main today (46) |
|---|--:|--:|
| A. as implemented: everything in the parent is unsorted | 14/44 = 31.8% | 14/46 = 30.4% |
| B. A, with the resolver defect fixed (`test_stop_hook.py` to Worker) | 13/44 = 29.5% | 13/46 = 28.3% |
| C. documents assigned by their `Modules:` line count as the parent's own files (in the total, not unsorted) | 4/44 = 9.1% | 4/46 = 8.7% |
| C'. as C, but those documents left out of the total | 4/34 = 11.8% | 4/36 = 11.1% |
| D. C, and package markers left out of both counts | 3/43 = 7.0% | 3/45 = 6.7% |
| D'. C' and D together | 3/33 = 9.1% | 3/35 = 8.6% |
| E. the suggested placements (note.py, test_note.py, test_stop_hook.py to Worker), counted as in A | 11/44 = 25.0% | 11/46 = 23.9% |
| E under C (and D) | 1/44 = 2.3% (0) | 1/46 = 2.2% (0) |

So the violation exists only under reading A, and only by one file; the resolver defect alone takes it below 30 percent. Under any reading that excludes the documents, the module is far below the threshold and the real structural question is two files (`note.py` and its test).

Recommendation: reading **C with D** (documents placed by `Modules:` line are own files; package markers are not counted). It matches the owner's wording, keeps the total honest (the documents are the module's files) and makes the share describe what the policy targets: code that the sub-module cut does not fit. Other modules are not affected today (`internal`: 6/42 = 14.3% under A; its unsorted files are tools without a sub-module, none are documents or markers).

## What the generic check needs (for the policy harness, PC-91)

- **Threshold as a named constant**: it exists, `UNSORTED_WARN = 0.30` in `logic.py`, compared strictly (`share > warn`), used by `ambiguity_view` and by the gaps detector (`kind: ambiguity`). The harness should reuse that one constant, not a second copy, and keep "more than" (strict).
- **Counting rule, stated in the contract** (`docs/contracts/card-metadata.md`, `ambiguity.json`), applied by the producer for every project, never per module:
  - `unsorted` = files resolved to a module that has sub-modules **and** either carry `candidates` or are decided only by a directory-prefix entry (no file-specific decision);
  - files resolved to the parent by a file-specific entry without `candidates` (the "about the whole module" decisions, including documents by their `Modules:` line) go to `own_files` of the parent, which today is forced to 0;
  - package markers (`__init__.py`; generically: a file name the map declares as marker) are counted nowhere;
  - total = `own_files + sum(sub_files) + len(unsorted)`, as now.
- **Resolver**: an exact entry for a test path must win over a mirror match that hits only a directory prefix (the `test_stop_hook.py` case), or the check reports wrong files with a wrong reason.
- **Evidence in the finding**: the module, the count, the total, the share, the reading used, and per file its candidates and reason, so that the finding is reproducible from the card data alone.
- **Tests**: project-agnostic fixtures (`module-a`, `module-a/one`) for: a parent document by `Modules:` line (not unsorted), an empty marker (not counted), a file with candidates (unsorted), a prefix-only file (unsorted), the shadowed test entry (resolved to its own entry), and the boundary (exactly 30 percent is no violation).

## Suggested decisions for the owner

1. Move `dispatcher/note.py` to `taskqueue/worker` (its test follows).
2. Fix the resolver so `tests/dispatcher/test_stop_hook.py` reaches its existing Worker entry (a task for `internal/metadata`).
3. Adopt counting reading C with D for every project; PC-91 implements it with the constant above.
4. Leave the ten documents and `__init__.py` where they are.
