"""A stand-in for a fine-tuned token-estimation model.

Given a description of a coding task, guess how many tokens an agent will
spend on it. The real thing would be a model on a GPU; this is a few plain
rules so the demo can focus on who gets to call it.
"""

# Tokens an agent spends on almost any task: reading context, planning, replying.
BASE_TOKENS = 8_000

# Words that tend to make a task bigger, and how much bigger.
EFFORT_WORDS = {
    "refactor": 2.0,
    "migrate": 2.5,
    "test": 1.4,
    "debug": 1.6,
    "fix": 1.2,
    "add": 1.1,
    "docs": 0.8,
    "rename": 0.7,
}


def estimate_tokens(task: str) -> dict:
    """Return a token estimate, a likely range, and how confident we are."""
    text = task.lower()
    words = text.split()

    # Longer descriptions usually mean bigger tasks.
    estimate = BASE_TOKENS + 400 * len(words)

    # Each effort word found scales the estimate.
    matched = [w for w in EFFORT_WORDS if w in text]
    for word in matched:
        estimate *= EFFORT_WORDS[word]

    # Mentioning files suggests more code to read.
    estimate += 3_000 * text.count(".py")

    estimate = round(estimate, -3)
    confidence = "medium" if matched else "low"
    spread = 0.4 if matched else 0.7

    return {
        "estimated_tokens": int(estimate),
        "range": [int(round(estimate * (1 - spread), -3)),
                  int(round(estimate * (1 + spread), -3))],
        "confidence": confidence,
        "signals": matched,
        "model": "token-estimator-stub-v0",
    }
