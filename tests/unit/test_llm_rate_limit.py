import threading

from lvnotes.core.config import LLMProfile
import pytest

from lvnotes.llm.rate_limit import ProfileRateLimiter, RateLimitBudget
from lvnotes.llm.rate_limit import acquire_profile_rate_limit, reset_profile_rate_limiters


@pytest.fixture(autouse=True)
def clear_profile_rate_limiters():  # type: ignore[no-untyped-def]
    reset_profile_rate_limiters()
    yield
    reset_profile_rate_limiters()


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def test_profile_rate_limiter_waits_for_rpm_window() -> None:
    clock = Clock()
    limiter = ProfileRateLimiter(clock.monotonic, clock.sleep)

    limiter.acquire(rpm_limit=1, tpm_limit=None, budget=RateLimitBudget(tokens=1))
    limiter.acquire(rpm_limit=1, tpm_limit=None, budget=RateLimitBudget(tokens=1))

    assert clock.sleeps == [60.0]


def test_profile_rate_limiter_waits_for_tpm_window() -> None:
    clock = Clock()
    limiter = ProfileRateLimiter(clock.monotonic, clock.sleep)

    limiter.acquire(rpm_limit=None, tpm_limit=10, budget=RateLimitBudget(tokens=6))
    limiter.acquire(rpm_limit=None, tpm_limit=10, budget=RateLimitBudget(tokens=5))

    assert clock.sleeps == [60.0]


def test_profile_rate_limiter_allows_single_request_over_tpm_limit() -> None:
    clock = Clock()
    limiter = ProfileRateLimiter(clock.monotonic, clock.sleep)

    limiter.acquire(rpm_limit=None, tpm_limit=10, budget=RateLimitBudget(tokens=20))

    assert clock.sleeps == []


def test_profile_rate_limiter_uses_longest_wait_when_rpm_and_tpm_apply() -> None:
    clock = Clock()
    limiter = ProfileRateLimiter(clock.monotonic, clock.sleep)

    limiter.acquire(rpm_limit=1, tpm_limit=10, budget=RateLimitBudget(tokens=4))
    clock.now = 20.0
    limiter.acquire(rpm_limit=None, tpm_limit=10, budget=RateLimitBudget(tokens=4))
    clock.now = 10.0
    limiter.acquire(rpm_limit=1, tpm_limit=10, budget=RateLimitBudget(tokens=3))

    assert clock.sleeps == [70.0]


def test_profile_registry_shares_limits_for_same_profile_name(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    clock = Clock()
    monkeypatch.setattr("lvnotes.llm.rate_limit.ProfileRateLimiter", lambda: ProfileRateLimiter(clock.monotonic, clock.sleep))
    reset_profile_rate_limiters()
    profile = LLMProfile(name="shared", provider="openai_compatible_chat", base_url="http://localhost:8000/v1", api_key_env=None, model="test", rpm_limit=1)

    acquire_profile_rate_limit(profile, 1)
    acquire_profile_rate_limit(profile, 1)

    assert clock.sleeps == [60.0]


def test_profile_registry_isolates_different_profile_names(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    clock = Clock()
    monkeypatch.setattr("lvnotes.llm.rate_limit.ProfileRateLimiter", lambda: ProfileRateLimiter(clock.monotonic, clock.sleep))
    reset_profile_rate_limiters()
    first = LLMProfile(name="first", provider="openai_compatible_chat", base_url="http://localhost:8000/v1", api_key_env=None, model="test", rpm_limit=1)
    second = first.model_copy(update={"name": "second"})

    acquire_profile_rate_limit(first, 1)
    acquire_profile_rate_limit(second, 1)

    assert clock.sleeps == []


def test_profile_registry_shares_limits_across_threads(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    clock = Clock()
    sleep_started = threading.Event()
    release_sleep = threading.Event()

    def blocking_sleep(seconds: float) -> None:
        clock.sleeps.append(seconds)
        sleep_started.set()
        release_sleep.wait(timeout=1)
        clock.now += seconds

    monkeypatch.setattr("lvnotes.llm.rate_limit.ProfileRateLimiter", lambda: ProfileRateLimiter(clock.monotonic, blocking_sleep))
    profile = LLMProfile(name="shared_threads", provider="openai_compatible_chat", base_url="http://localhost:8000/v1", api_key_env=None, model="test", rpm_limit=1)
    acquire_profile_rate_limit(profile, 1)
    waiting = threading.Thread(target=lambda: acquire_profile_rate_limit(profile, 1))

    waiting.start()
    assert sleep_started.wait(timeout=1)
    release_sleep.set()
    waiting.join(timeout=1)

    assert not waiting.is_alive()
    assert clock.sleeps == [60.0]


def test_profile_rate_limiter_is_thread_safe() -> None:
    clock = Clock()
    sleep_started = threading.Event()
    release_sleep = threading.Event()

    def blocking_sleep(seconds: float) -> None:
        clock.sleeps.append(seconds)
        sleep_started.set()
        release_sleep.wait(timeout=1)
        clock.now += seconds

    limiter = ProfileRateLimiter(clock.monotonic, blocking_sleep)
    limiter.acquire(rpm_limit=1, tpm_limit=None, budget=RateLimitBudget(tokens=1))
    waiting = threading.Thread(target=lambda: limiter.acquire(rpm_limit=1, tpm_limit=None, budget=RateLimitBudget(tokens=1)))

    waiting.start()
    assert sleep_started.wait(timeout=1)
    release_sleep.set()
    waiting.join(timeout=1)

    assert not waiting.is_alive()
    assert clock.sleeps == [60.0]
