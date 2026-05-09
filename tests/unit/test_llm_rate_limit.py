from lvnotes.llm.rate_limit import ProfileRateLimiter, RateLimitBudget


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
