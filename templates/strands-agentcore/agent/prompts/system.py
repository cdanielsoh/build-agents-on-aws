"""File-based system prompt builder.

Pattern: Load system prompt from .md files at startup. The content is
version-controlled, diff-able, and cacheable by Bedrock's prompt prefix.
Changes to the prompt only require a redeploy, not a code change.

For multi-section prompts, compose from multiple files:

    def build_system_prompt(include_rules=True):
        parts = [_load("identity.md")]
        if include_rules:
            parts.append(_load("rules.md"))
        return "\\n\\n".join(parts)
"""

from pathlib import Path

_PROMPT_DIR = Path(__file__).parent


def build_system_prompt() -> str:
    """Build the static system prompt.

    In production, load from .md files:
        return (_PROMPT_DIR / "system.md").read_text()

    This inline version is for scaffold demonstration.
    """
    return (
        "You are a helpful customer support agent.\n"
        "You help users with account inquiries, order tracking, and general questions.\n\n"
        "Available tools retrieve data for the authenticated user automatically — "
        "no user ID parameters are needed.\n\n"
        "Guidelines:\n"
        "- Call the appropriate tool immediately when a user asks about their account or orders.\n"
        "- Explain information in plain language.\n"
        "- Provide actionable next steps when relevant."
    )
