# Would a local LLM gateway such as LiteLLM add value to token accounting?

Status: research note (task #141), written by the interactive session on the owner's request, 2026-11-25. Sources are public documentation read on that date; anything not confirmed in a source is marked **unverified**.
Date: 2026-11-25
Modules: taskqueue

## Recommendation

**Do not adopt now. Adopt later only under the conditions below.** The counting and attribution it offers are real, but they are a convenience over what the Task Queue proxy already does for Workers. It does not cover the quota windows. And it would hold the subscription credential while having two documented supply-chain compromises in 2026.

## What it would add

- **One counting point for every client** that is pointed at it by base URL (Workers, interactive sessions, sub-agents), instead of the proxy's usage file, session transcripts and a merging script. Source: [Claude Code gateway page of LiteLLM](https://docs.litellm.ai/docs/proxy/client_setup/claude_code).
- **Attribution through headers.** Spend is attributed per virtual key, customer, member and tag, set through `ANTHROPIC_CUSTOM_HEADERS` (`x-litellm-tags`, `x-litellm-customer-*`); without a header each Claude Code session appears as its own customer. Source: [Claude Code customer tracking](https://docs.litellm.ai/docs/tutorials/claude_code_customer_tracking).
- **Subscription logins are forwarded** (from v1.81.14): the client's OAuth token goes to direct `anthropic/` deployments, and the spend log flags such rows with `used_client_oauth_token=true`. Source: [Claude Code with a Max subscription](https://docs.litellm.ai/docs/tutorials/claude_code_max_subscription).
- **Free tier covers the accounting core:** virtual keys, budgets and rate limits, spend tracking. Tag budgets and audit logs are in the paid Enterprise tier. Source: LiteLLM's enterprise pages as summarised by a search, so treat the split as **unverified** until read on [the official page](https://docs.litellm.ai/docs/enterprise) (the page fetched directly returned 404 for one URL; the licence text of the repository's enterprise folder could not be read, and GitHub reports the repository licence as "other").

## What it would not cover

- **The quota windows.** The 5-hour and weekly percentages come from vendor response headers. LiteLLM's cost is reported at API list prices even for subscription requests, so its budgets are in "API-equivalent dollars" (same Max-subscription page). Whether it returns or records the vendor's rate-limit headers is **not stated in the documentation read; unverified**. The quota collector stays ours unless a test proves header passthrough.
- **Cache tokens.** An issue thread says the cost helper treats `cache_read_input_tokens` separately for Anthropic, and an older Claude Code issue reported zero cache tokens through a proxy. The pages read do not name the cache-read and cache-creation fields in the spend logs; **unverified**. These fields matter because their quota burn differs. ([pass-through cost tracking](https://docs.litellm.ai/docs/proxy/pass_through_cost_tracking), [Claude Code issue](https://claudeissues.com/issue/1756-bug-cost-not-calculated-for-llm-gateway-interactions)).
- **Task-state authority and the GitHub credential**, which are a different problem from LLM traffic.

## Cost and weight

- Virtual keys, budgets and per-request usage are stored in PostgreSQL ([what is stored in the DB](https://docs.litellm.ai/docs/proxy/db_info)); the page does not mention SQLite. The Max-subscription page states PostgreSQL as part of that setup. So it needs a running database and a running service.
- It forwards the subscription token, so it would hold the credential that everything depends on.

## Security history (decisive for a credential-holding gateway)

Two incidents in 2026 are reported:
1. **2026-03-24, PyPI versions 1.82.7 and 1.82.8**: published by an attacker who obtained maintainer credentials through an earlier compromise of a security scanner; PyPI quarantined the package; the Docker image was reported unaffected because dependencies were pinned. Sources are secondary ([JFrog](https://research.jfrog.com/post/litellm-compromised-teampcp/), [Snyk](https://snyk.io/articles/poisoned-security-scanner-backdooring-litellm), [NetSPI](https://www.netspi.com/blog/executive-blog/ai-ml-pentesting/litellm-supply-chain-compromise/)); the primary advisories were not opened, so version details are **unverified**.
2. **2026-10-21, PyPI version 1.107.0 and the Docker tags `v1.107.0`, `latest`, `main-latest`, `main-stable`**: live for about two hours; attackers used stolen GitHub CLI tokens of three engineers to run the release workflow against a look-alike fork; the payload ran at every Python start and sent cloud, AI-tool and LiteLLM proxy credentials to an attacker domain; the last safe version was reported as 1.106.2. Source: [Datadog Security Labs](https://securitylabs.datadoghq.com/articles/litellm-compromised-pypi-docker.md), fetched directly. A second report is [BleepingComputer](https://bleepingcomputer.com/news/security/litellm-release-pipeline-hacked-to-distribute-info-stealer-malware) (not opened).

## Conditions under which adoption would be reconsidered

1. A test that shows the vendor's rate-limit headers and the cache-read and cache-creation token counts reach the client and the spend log unchanged, with the subscription login.
2. The release pinned by digest, built or verified independently of the vendor's pipeline, never `latest`.
3. The gateway runs under its own account or container with no other credentials on it (see the discussion of the gate and a separate interactive account, F-976 to F-979).
4. A real need that the current proxy cannot meet: per-project budgets, local models next to the hosted one, or routing.

## A smaller alternative worth considering first

Extend the existing Task Queue proxy to record the cache-read and cache-creation counts and a task tag per request, and point interactive sessions at it by base URL if one counting point is wanted. This keeps one small, auditable component that already holds the credential and already reads the quota headers. **Unverified** as to effort; it needs a design look before any task.
