"""Reject inherited identity/routing overrides; never inspect secret values."""

import os


AUTH_ENV_OVERRIDES = {
    "codex": frozenset({
        "OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN", "OPENAI_BASE_URL",
        "OPENAI_ORGANIZATION", "OPENAI_ORG_ID", "OPENAI_PROJECT", "OPENAI_PROJECT_ID",
        "OPENAI_FEDERATION_RULE_ID", "OPENAI_IDENTITY_TOKEN_FILE", "CODEX_HOME",
    }),
    "claude": frozenset({
        "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "ANTHROPIC_CUSTOM_HEADERS",
        "ANTHROPIC_AWS_API_KEY", "ANTHROPIC_AWS_BASE_URL", "ANTHROPIC_AWS_WORKSPACE_ID",
        "ANTHROPIC_BEDROCK_BASE_URL", "ANTHROPIC_BEDROCK_MANTLE_BASE_URL",
        "ANTHROPIC_FOUNDRY_API_KEY", "ANTHROPIC_FOUNDRY_AUTH_TOKEN", "ANTHROPIC_FOUNDRY_BASE_URL",
        "ANTHROPIC_FOUNDRY_RESOURCE", "ANTHROPIC_VERTEX_BASE_URL", "ANTHROPIC_VERTEX_PROJECT_ID",
        "ANTHROPIC_ORGANIZATION_ID", "ANTHROPIC_FEDERATION_RULE_ID", "ANTHROPIC_IDENTITY_TOKEN_FILE",
        "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_OAUTH_REFRESH_TOKEN", "CLAUDE_CODE_OAUTH_SCOPES",
        "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
        "CLAUDE_CODE_USE_ANTHROPIC_AWS", "CLAUDE_CODE_SKIP_ANTHROPIC_AWS_AUTH",
        "CLAUDE_CODE_SKIP_BEDROCK_AUTH", "CLAUDE_CODE_SKIP_VERTEX_AUTH", "CLAUDE_CODE_SKIP_FOUNDRY_AUTH",
        "CLAUDE_CODE_SIMPLE", "CLAUDE_CONFIG_DIR",
    }),
}
# The Agent SDK launches the same claude executable, so the same overrides are refused.
AUTH_ENV_OVERRIDES["claude-agent"] = AUTH_ENV_OVERRIDES["claude"]


def assert_auth_environment(provider, environment=None):
    # Iteration obtains keys only; Mapping.__contains__ can internally call
    # __getitem__ on os._Environ, so do not use membership on that mapping.
    names = frozenset(os.environ if environment is None else environment)
    if any(name in names for name in AUTH_ENV_OVERRIDES.get(provider, ())):
        # Even an empty override is refused. Reading its value is unnecessary.
        raise ValueError("Authentication or routing environment override is present; use reviewed local credential storage")
