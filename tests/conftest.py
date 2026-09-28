"""Shared test isolation."""
import pytest

from bardic.tts_limits import LIMITER


@pytest.fixture(autouse=True)
def fresh_rate_limiter():
    # The Gemini speech limiter is process-wide by design; a 429 cooldown in
    # one test must not pace the next test's offline requests.
    LIMITER.reset()
    yield
    LIMITER.reset()
