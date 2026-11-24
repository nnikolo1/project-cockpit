# Project task system · Problem Statement

## Objective: agent-assisted software development in a nutshell, opinionated (owner, 2026-11-24)

A pocket-size Swiss Army knife for agent-assisted software development: **in a nutshell** and **opinionated**. It combines several tools at once (continuous integration, quality gates, a queue, an agent manager, an artefact store, policy checks), each at a minimal level, as plain files and small commands, with no heavy framework; the combination is the point. Where a choice has to be made, the Cockpit makes it and says so; it does not offer every option. Every rule below has an enforcing mechanism.

Over the course of a few months the rate of code generation outran the ability of the human stake-holder to keep the project knowledge in his cognitive context window. Yet for the success of the project this is essential.

The information exchange between the agent and the human was sub-optimal which impeded both parties:

- the human found it difficult to keep track of the code-base and its alignment to the project goals;
- all too often the agent was staying idle as the human was slow in assessing the work done and assigning the next tasks.

The goal of this system is to better adjust the human-agent interaction to the inherent mismatch in data bandwidth and context window size between the two parties.

## Working Hypothesis

The working hypothesis of this project is that a better mapping of the agent-assisted software engineering project onto the traditional scaffold of a software engineering project may alleviate the issues encountered.

What are the features of the traditional software engineering project management which appears to be lacking (or not sufficiently well developed) in the agent-assisted software engineering project:

- strict separation of roles and responsibilities;
- documentation which targets human understanding.

The traditional (i.e. human-powered) software engineering project has a strict separation of roles and responsibilities:

- product manager
- project lead / manager
- team lead
- architect
- software engineers (which themselves tend to be split into a tech lead, regular engineers and junior engineers)
- DevOps engineers
- QA or test engineers

The above is a generalized description. Depending on project size some of these roles can be merged or further sub-divided into more specialized roles (e.g. backend or front-end engineers, data/database engineers, AI/ML engineers, UX designers, data scientists, etc.)

This separation is driven partially by the need for specialised expertise, but also because of the inherent human cognitive limitation - from certain project size up no single human can hold the entire project knowledge in his/her cognitive context window. The fact that AI agents can combine all these expert areas leads to the  temptation to merge them. An agent has been exposed during its pre-training (and, potentially, during the fine-tuning or RL phase) to all these specialised domains. Yet, role separation does not seem completely unnecessary - during the course of the project through trial and error it was established that a suitable SOP for ADRs is to do it in at least 2 rounds: in the first two different instances of the same AI agent come up with proposals independently of each other; in the second they critique each others solutions. This can be iterated till consensus emerges. This heuristic may not hold in all situations but illustrates that role speciation may be appropriate even for AI agents.

## Principles

More generally, the structure of the human-powered software engineering project exhibits features typical of human organisations - both the need for centralised coordination, and the need for  "checks and balances". This project management system will attempt to map onto this archetypal structure of human organisations in putting the human in the driver seat and introducing role separation between agents.

It will also try to address the mismatch in information bandwidth and context window between the humans and  agents by increasing the effort not just to capture the project knowledge in human-understandable form but also to apply information compression and aggregation so that the it can be consumed by a human quickly and efficiently. The idea is to create a "digital cockpit" which will provide a quick overview of important metrics and instruments so that the decisions can be made faster. Guided by the principle "a picture is worth thousand words" there will be increased emphasis on visualisations.

The human behind this project management system is acutely aware that there are dozens, if not hundreds, of such project management systems. Thus there will be a preference towards "buy" (or adopt) vs. "build". Yet, there is no fear in threading new ground - the super-human power of the agents drastically lowers the cost of code generation. There will be no fear in yielding this super-power judiciously. The goal is to come up with a project-management system which is light-weight, as general (and generalizable) as possible,  and leverages as much of the pre-existing tooling and infrastructure, as practical.

Scope (owner, 2026-11-24): besides viewing, the Project cockpit has an element of continuous integration and delivery and of an artefact store: it builds what its projects need (collectors, snapshots, reports, archives), checks it (the policy harness), delivers its own releases and managed files to the projects, and keeps the generated artefacts in a known place with their age and version; it stays small: plain folders and manifests before any registry.

The constitution is not aspirational (owner, 2026-11-24): every rule has an enforcing mechanism (a policy check, a gate in the Dispatcher or the merge step, or a generated artefact), and a rule without one is reported as a gap. The Cockpit is an opinionated combination of continuous integration, code quality gates, a queue and an agent manager.

Wrinkles on the path to automation are removed (owner, 2026-11-24): a step still done by hand is either automated or has an open task, and the manual-interventions log is the list.

Continuous integration is automatic (owner, 2026-11-24): no check, gate or report depends on a person remembering to run it; "by hand" is an oxymoron for CI.

## Amendment: the Interactive Agent merges unmerged tasks (owner, 2026-11-24)

The Interactive Agent has the authority to merge tasks in the state `unmerged`, and does so unprompted. Before merging it verifies (1) that the commit matches the task brief and (2) that it obeys all policies (tests passing included). If both hold, it merges. If no policy is violated and no test fails but the commit does not meet the brief fully, it merges and files a task meant to finish the job. If a policy is violated or a test fails, it does not merge: it formulates a new follow-up task whose work is to remove the existing issues (owner, 2026-11-24, constitutional amendment). The Interactive Agent may delegate the merge to a background agent by supplying it with this brief; for that merge the background agent holds the privileges of the Interactive Agent, so it can merge and formulate tasks; in this way the Interactive Agent can parallelise the merging (owner, 2026-11-24). Merging is parallelised whenever conflicts can be avoided (branches that touch disjoint or cleanly combinable files go to separate merge agents at once; the others are merged in turn) (owner, 2026-11-24, standing policy).

Findings are released by the finder and worked by someone else (owner, 2026-11-24): A policy violation identified by the interactive agent may be filed and released by it as a task, to be worked by a background agent (a Worker); the finder itself never works the task, so the separation of powers holds. The release applies only to tasks labelled policy-finding that carry the policy sentence and the evidence, with a trail comment naming the policy (owner, 2026-11-24; amends the policy-finding rule of the same day).

Policy is code (owner, 2026-11-24): a standing policy is a check that a program can run, and a violation is like a failing test in a coding harness: it automatically triggers remediation, a task filed and released at once, which the finder does not work on itself. The checks and the remediation are an extension of the coding agent's own harness, run by the daily job for every project the Cockpit serves; the owner decides policies, the harness enforces them.

The host operating system does not dictate the architecture (owner, 2026-11-24): the system is not bent to fit one operating system or one version of it; where the current host needs workarounds, they are a cost to weigh against moving, not a requirement.

Any text or code produced follows the principle "less is more": it is included only if it is absolutely necessary or specifically asked for by the owner.

## The Definition of a Task

The "task" is the most basic entity in the project management system. It can be many types - a bug fix, a feature implementation or something else. It can consist of other tasks (and thus tasks may form trees of sub-tasks) and it could depend on other tasks. A task is about change of state - the creation, update or deletion of a file (e.g. code, documentation, configuration setting, database record). This is a (preliminary) list of its attributes:

| Field | Meaning |
|---|---|
| id, parent id | Position in the tree: project, module, work package, task, subtask. Parent is nullable. |
| title, description | What and why. |
| type | a(preliminary) enum: feature, test, bug, decision, research, a DevOps (settings, infrastructure) or  documentation artefact (report, visualisation, etc.). |
| class | must-have or nice-to-have. Must-haves are on the critical path of the project. |
| deadline | Optional date. |
| priority | 1-3 (highest - lowest) |
| stage | See below |

There can be relations between tasks. It could be a "vertical" 1:n relationship ("composed of") or "horizontal" m:n relationships ("blocked by", "related to"). Example of the latter: a bug may be related to a discrete feature (which itself sis a task).

### Context management

Managing the context of the agents' sessions is part of task management (owner, 2026-11-24): what a Worker knows when it starts is as much a part of a task as its description. A Worker session is attached to its project (its working directory, its project memory and settings) and receives, besides the task, a short dated synopsis of the latest interactive session of that project, which is kept outside every repository; the synopsis and the project memory are written without secrets or personal data, are never copied into commits, and go stale visibly. What the Interactive Session Agent learns in a session that binds later work (decisions, standing rules, open questions) is handed on this way, not left in the conversation.

## The Life Stages of a Task

Much like a human, a task begins its life as an embryo: it has been conceived by a human or an agent. An embryo created by the Interactive Session Agent counts as released once 10 days have passed since its creation; an embryo created by the Owner counts as released at once. A Worker does not create tasks: it may suggest one in its good-bye note, and the Interactive Session Agent that mops up decides whether to file tasks from it. A released task can be assigned by the Dispatcher to a Worker, and while the Worker works on it the task is WIP. When the Worker has finished, the task is unmerged, or failed if the Worker gave up or left no usable result. Once the Interactive Session Agent has merged its branch into the main branch, the task is merged. Only the Owner or an interactive agent can change the status of a task once it is merged or failed. A task filed by an agent because it found a file that contradicts a standing policy counts as released at once; the agent that found the contradiction does not work on that task.

## The Roles

The roles are the Owner, the Dispatcher, the Workers and the Interactive Session Agent.

### The Owner

This is the human, acting via the Cockpit GUI.

### The Dispatcher

This is a deterministic procedure, not an AI agent. It runs on the host and assigns released tasks to Workers, following priority and taking deadlines and blockers into account. When a Worker has ended, it decides from the Worker's good-bye note whether the task is unmerged or failed. There is one Task Queue, and it serves all the owner's projects: one Dispatcher, one pool of Workers, one ranking across the projects, in which the Project cockpit, being the meta-project that provides the bedrock for all projects, is the top-priority project unless the owner says otherwise, and all the other projects are equal unless the owner says otherwise (owner, 2026-11-24, a constitutional setup; how projects are ranked against each other and which are served is decided separately). It follows that the processing data (the processing log, the Dispatcher's state, the Workers' area, the proxy and its usage file) is stored in one place for all projects, with a project field on each record, and not in one folder per project. A task about the Task Queue itself is never run through it: it is worked outside the queue, as a high-priority task, by the Interactive Session Agent.

### The Interactive Session Agent

This is an AI agent working with the Owner in an interactive session. It merges the Workers' results into the main branch and then sets the task to merged.

### A Worker

This is a headless AI agent that works on one task, in its own clone, as an unprivileged user in a container. Multiple Workers may be active at any given time.
