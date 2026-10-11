"""Token estimation.

No tokenizer is downloaded in the default install, so token counts are *estimated* with a character
heuristic and always flagged `estimated=True`. When a provider reports usage, actual counts replace
estimates. Story points are never derived from these numbers.
"""

from __future__ import annotations

import math

CHARS_PER_TOKEN = 4.0
METHOD = "chars/4 heuristic"


def estimate_tokens(text: str) -> int:
    return max(1, math.ceil(len(text) / CHARS_PER_TOKEN)) if text else 0


def estimate_messages(messages: list[str]) -> int:
    # ~4 tokens of chat-template overhead per message
    return sum(estimate_tokens(m) + 4 for m in messages)
