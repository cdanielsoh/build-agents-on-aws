"""File-based system prompt builder.

Pattern: Load the system prompt from .md files at startup. The content is
version-controlled, diff-able in PRs, and forms the cached prefix of every request.
Changing the prompt is a redeploy, not a code change.

Keep these files STATIC. Anything that varies per request or per user belongs in a
tool result, not here — see references/prompt-architecture.md.
"""

from functools import cache
from pathlib import Path

_PROMPT_DIR = Path(__file__).parent


def _load(name: str) -> str:
    return (_PROMPT_DIR / name).read_text(encoding="utf-8").strip()


@cache
def build_system_prompt() -> str:
    """Build the static system prompt.

    Cached because the result is identical for the life of the container and is read
    on every session build.

    To compose from multiple files — useful when sections are owned by different
    people, or when a section is conditional:

        parts = [_load("identity.md"), _load("rules.md")]
        if include_escalation:
            parts.append(_load("escalation.md"))
        return "\\n\\n".join(parts)

    Order matters for caching: put the most stable sections first.
    """
    return _load("system.md")
