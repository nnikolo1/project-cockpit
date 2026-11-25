# Architecture decision records

Short records of why the cockpit is the way it is. One decision per file, one screen each: context, decision, consequences (including what we gave up), and when to revisit. Open items are in [the roadmap memo](../memos/2026.11.17-roadmap.md).

| No. | Title | Status |
|---|---|---|
| [0001](0001-github-is-the-store-and-identity-provider.md) | GitHub is the store and the identity provider | accepted |
| [0002](0002-opinionated-subset-of-github-semantics.md) | An opinionated subset of GitHub semantics | accepted |
| [0003](0003-protected-attributes-are-labels.md) | Protected attributes are labels, trusted by their actor | accepted |
| [0004](0004-read-mostly-app-with-opt-in-steering.md) | Read-mostly app, opt-in steering, local by default | accepted |
| [0005](0005-modules-and-sub-modules.md) | Modules and sub-modules are configured, not created in the app | accepted |
| [0006](0006-deadlines-and-ranking.md) | Deadlines are optional; the ranking is fixed and visible | accepted |
| [0007](0007-rule-change-tracking.md) | Rule-change tracking (the Rules tab) | accepted |
| [0008](0008-code-intelligence-from-a-snapshot.md) | Code intelligence is consumed from a snapshot, not computed | accepted |
| [0009](0009-stdlib-only-small-code-pure-tests.md) | Stdlib only, small code, pure-logic tests | accepted |
| [0010](0010-rate-limit-resilience.md) | Rate-limit resilience: cache, stale-on-error, one call | accepted |
| [0011](0011-deployment-and-role-separation.md) | Deployment: one cockpit per repo, role separation by identity and credentials | accepted |
| [0012](0012-several-projects-in-one-instance.md) | Several projects in one instance, chosen by a selector | accepted |
| [0013](0013-card-metadata-from-generated-files.md) | Card metadata (churn, footprint, ambiguity) comes from generated files | accepted |
| [0014](0014-boot-filling-job.md) | The boot-filling job releases ranked tasks to use quota that would expire (idle only, 30-minute poll, fixed payloads, limits, full log) | accepted |
| [0015](0015-boot-filling-implementation-choices.md) | Boot-filling job: implementation choices beyond ADR 0014 (proposed) | proposed |
| [0017](0017-boot-filling-quota-history.md) | Boot-filling job: quota history with token counts, one record per poll (proposed) | proposed |
| [0018](0018-boot-filling-stale-quota-readings.md) | Boot-filling job: stale or missing quota readings are unknown or estimated, never invented (amends 0017) | proposed |
| [0019](0019-boot-filling-never-twice.md) | Boot-filling job: released once, worked once; silent GitHub timeline thinning fixed; the worker marks in progress (amends 0014, 0015) | proposed |
| [0020](0020-boot-filling-worker-privileges-tier-a.md) | Boot-filling job: worker privileges tier A (tool lists, no symlinks, role marker, rules block); what is enforced and what is not | proposed |
| [0021](0021-ci-cd-and-artefact-store.md) | The Cockpit has a small element of CI/CD and an artefact store | accepted |
| [0022](0022-policy-packs-and-optional-module-map.md) | Policy packs stored where Claude reads them; the module map is a setting | accepted in direction |
