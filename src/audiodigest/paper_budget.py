"""Conservative optional-paper admission; not a synthesis time guarantee."""

import math

PAPER_MINIMUM_SECONDS = 180
PAPER_MAXIMUM_SECONDS = 600


def completion_reserve(word_count: int | None) -> int:
    # Preserve the legacy floor, then scale for longer verified scripts. Includes
    # voice setup, headings/disclosure, encoding, publication and promotion margin.
    # No historical private content or new provider read is required.
    if word_count is None:
        return 900
    if isinstance(word_count, bool) or not isinstance(word_count, int) or word_count < 0:
        raise ValueError("Invalid verified script word count")
    return max(900, math.ceil(word_count * 0.55) + 180)


def paper_allowance(remaining: float | None, reserve: int) -> float | None:
    if remaining is None:
        return None  # Do not impose a new timeout on local desktop generation.
    return min(PAPER_MAXIMUM_SECONDS, max(0.0, remaining - reserve))
