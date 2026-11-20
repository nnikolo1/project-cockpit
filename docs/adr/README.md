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
