# Project task system · Problem Statement

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

## The Life Stages of a Task

Much like a human, a task begins its life as an embryo - it has conceived either by a human or an agent. The "embryo" is merely recorded as "conceived" in the Event Log of the project management system. It springs to life once it is "released" by the Manager. Once it is released it can be assigned by the Orchestator to a Worker. Once it is assigned to a Worker it starts its (usually brief) "in progress" stage. Then it will be "completed".

## The Roles

We have already mentioned the 3 main roles:

### The Manager

This is the human. It is only him/her who can "release" tasks.

### The Orchestrator

This is an AI agent which is, essentially, a middleman between the Manager and the Workers. It assigns tasks to workers, and receives (and, potentially, validates) the output. The Orchestrator is the one merging and pushing from work-trees into the main branch of the git repo. It can create tasks and change their attributes. It is the only agent able to write to the Event Log. However, certain type of events can be entered into the Event Log only by the Manager or need to be approved by him/her: the release of the task, the change of priority, the setting or change of a deadline. The write rules related to the Event Log are enforced by a IAM, not by a LLM policy.

### A Worker

This is an AI agent. It may be a specialised one or not. Multiple Workers may be active at any given time. Each worker will write to a separate work tree in the git repo.

## The Event Log

The main data structure is the append-only Event Log. Any change of state is recorded there.
