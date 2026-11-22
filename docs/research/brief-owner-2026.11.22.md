# Research brief: orchestration of unattended AI assistant work

Written by the owner, 2026-11-22 (lightly corrected for spelling; one personal detail made generic because the repository is public). This brief supersedes the assistant-written brief `orchestration-brief-2026.11.22.md`, which is kept only as history.
Modules: docs

## 1. Problem statement

The owner is a solo human developer who builds software with AI coding assistants, across several projects. So far, the only AI agent used has been Claude running within Claude Code (CC).

His first approach was to carefully prepare a PRD document (over the course of several days) and let the AI agent generate code based on it. Over time he learned to trust AI more, and development took place in interactive sessions with requirements spelled out over a series of turns.

While the progress has been nothing short of remarkable, some problems were discovered:

- The pace of work would overflow the cognitive context window of the developer: he would lose track of what was discussed and what was implemented. He realised that he was missing the classical structure of a software engineering project as provided by popular tools like Jira, Confluence, Trac, etc.: kanban boards, backlog issues and tickets, etc.
- At the same time, he did not want to simply adopt an existing tool, as he felt that such tools include a lot of functionality he does not need. Also, he is cost-conscious: paying for such tools is acceptable, but the budget is modest (say, no more than 20 euros per month). The other problem which bothered him was that he was already using GitHub, and using another hosted service felt like introducing a separate single source of truth (SSOT). This may or may not be a valid concern; he is open to this being challenged.

This is the reason why he started the "Project Cockpit" (PC) GitHub repository: it was meant to be a thin client over GitHub issues. The idea was to record there a list of the project tasks.

Almost as an afterthought, another problem and opportunity was spotted:

- The developer upgraded his CC subscription in October and realised that he was not able to exhaust it (routinely using approximately 20%) while many tasks were recorded as "not done". He realised that this is an inherent limit of the way he was using CC: interactive sessions. He needs to attend to other real-life commitments, and this means he cannot fully utilise the computing quotas: the interactive sessions pause when he is away from the keyboard.
- He envisioned a way for CC to run unattended, picking unresolved tasks from a sort of queue while he is away from the keyboard. So far most of the thinking was about creating different agent roles. The two main ones are the Orchestrator (privileged agent sessions), which assigns the tasks to Workers (headless sessions committing to local worktrees), with the Orchestrator being the "Master of Puppets": assigning tasks to the Workers and then accepting (or not) their work. This split was meant, among other things, to enforce a QA aspect and to separate the work done from its verification. He formulated this earlier using the "Playing Coach" and "Janitor-Manager" analogies, which he incorporated in CLAUDE.md and other policy files.

CC diligently started implementing these ideas as part of the GitHub repository of Project Cockpit. You are encouraged to consult that repository.

It is only recently that the developer realised that he is, in effect, trying to "re-invent the wheel". The unattended execution of tasks has been a problem which computer science has been trying to solve for decades; dozens of approaches (implemented as open source or commercial projects) exist: orchestrators, workflow engines, cluster managers. To mention just a few: BPMN/CMMN engines (e.g. Orkes, n8n, Flowable), cluster managers (e.g. SGE, LSF), orchestrators and workflow managers (e.g. Spark, Slurm, Nextflow). The problem is, actually, a generic one: constructing computational graphs (the unresolved tasks sometimes depend on each other, which limits parallel execution), lazy execution, and optimal allocation of a constrained resource (token budgets, which have both 5-hour windows and weekly limits). These are issues which not just computer science but also operations research have been working on for decades.

It appeared sub-optimal to try to create such a solution from scratch. Also, he realised that these problems naturally arise in the context of AI agent orchestration. He realised that, most likely, he is not the first one thinking about this problem in the context of AI agent orchestration.

## 2. Goals

So the goal of this research is to identify the state-of-the-art (SOTA) solutions for the above problems, first and foremost in the context of AI agent orchestration.

The idea is to, if possible, adopt rather than build from scratch such a solution as part of the Project Cockpit project.
