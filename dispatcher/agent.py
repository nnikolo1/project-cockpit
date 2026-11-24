"""The agent the Workers run (spec docs/specs/dispatcher-and-workers.md: 4f, 4i, 3o): what the Dispatcher
needs to know about it, in one place. `ClaudeCode` is the one implementation."""
import abc


class Agent(abc.ABC):
    @abc.abstractmethod
    def command(self, prompt):
        """The headless command line (inside the container) that runs the agent on the prompt."""

    @abc.abstractmethod
    def env(self, user, base_url, token):
        """The environment variables that point the agent at the model API and its per-user configuration."""

    @abc.abstractmethod
    def activity_glob(self, user):
        """A glob (inside the container) of files that the agent updates while it works (3o)."""


class ClaudeCode(Agent):
    # 4i: Claude Code's non-essential traffic off; skipWebFetchPreflight is set in the image's settings.json
    SWITCHES = {
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "DISABLE_TELEMETRY": "1", "DISABLE_ERROR_REPORTING": "1",
        "ENABLE_CLAUDEAI_MCP_SERVERS": "false", "CLAUDE_CODE_DISABLE_ARTIFACT": "1",
        "CLAUDE_CODE_DISABLE_OFFICIAL_MARKETPLACE_AUTOINSTALL": "1",
    }

    def command(self, prompt):
        return ["claude", "-p", prompt]

    def env(self, user, base_url, token):
        return dict(self.SWITCHES, ANTHROPIC_BASE_URL=base_url, ANTHROPIC_AUTH_TOKEN=token,
                    CLAUDE_CONFIG_DIR=f"/home/{user}/.claude")

    def activity_glob(self, user):
        return f"/home/{user}/.claude/projects/**/*.jsonl"
