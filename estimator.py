"""A stand-in for a fine-tuned token-estimation model.

Given a description of a coding task, a real model would guess how many tokens
an agent will spend on it. This stub always answers 10,000, so the demo can
focus on who gets to call it. Swap this function for a real model later.
"""


def estimate_tokens(task: str) -> dict:
    """Return a fixed token estimate for any task."""
    return {
        "estimated_tokens": 10_000,
        "model": "token-estimator-stub-v0",
        "note": "Stub model: always estimates 10,000 tokens.",
    }
