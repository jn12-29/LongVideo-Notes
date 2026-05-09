from collections import deque
from dataclasses import dataclass
import threading
import time
from typing import Callable

from lvnotes.core.config import LLMProfile

_WINDOW_SECONDS = 60.0


@dataclass(frozen=True)
class RateLimitBudget:
    requests: int = 1
    tokens: int = 0


class ProfileRateLimiter:
    def __init__(self, now: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> None:
        self._now = now
        self._sleep = sleep
        self._lock = threading.Lock()
        self._requests: deque[float] = deque()
        self._tokens: deque[tuple[float, int]] = deque()

    def acquire(self, rpm_limit: int | None, tpm_limit: int | None, budget: RateLimitBudget) -> None:
        if rpm_limit is None and tpm_limit is None:
            return
        token_cost = max(0, budget.tokens)
        request_cost = max(0, budget.requests)
        while True:
            with self._lock:
                now = self._now()
                self._prune(now)
                wait_seconds = self._wait_seconds(now, rpm_limit, tpm_limit, request_cost, token_cost)
                if wait_seconds <= 0:
                    for _ in range(request_cost):
                        self._requests.append(now)
                    if token_cost > 0:
                        self._tokens.append((now, token_cost))
                    return
            self._sleep(wait_seconds)

    def _prune(self, now: float) -> None:
        cutoff = now - _WINDOW_SECONDS
        while self._requests and self._requests[0] <= cutoff:
            self._requests.popleft()
        while self._tokens and self._tokens[0][0] <= cutoff:
            self._tokens.popleft()

    def _wait_seconds(
        self,
        now: float,
        rpm_limit: int | None,
        tpm_limit: int | None,
        request_cost: int,
        token_cost: int,
    ) -> float:
        waits: list[float] = []
        if rpm_limit is not None and request_cost > 0 and len(self._requests) + request_cost > rpm_limit:
            index = len(self._requests) + request_cost - rpm_limit - 1
            waits.append(self._requests[index] + _WINDOW_SECONDS - now)
        if tpm_limit is not None and token_cost > 0:
            used_tokens = sum(tokens for _, tokens in self._tokens)
            if token_cost > tpm_limit:
                return 0.0
            if used_tokens + token_cost > tpm_limit:
                excess = used_tokens + token_cost - tpm_limit
                released = 0
                for timestamp, tokens in self._tokens:
                    released += tokens
                    if released >= excess:
                        waits.append(timestamp + _WINDOW_SECONDS - now)
                        break
        return max(waits, default=0.0)


_limiters: dict[str, ProfileRateLimiter] = {}
_limiters_lock = threading.Lock()


def acquire_profile_rate_limit(profile: LLMProfile, token_budget: int) -> None:
    if profile.rpm_limit is None and profile.tpm_limit is None:
        return
    limiter = _limiter_for(profile.name)
    limiter.acquire(profile.rpm_limit, profile.tpm_limit, RateLimitBudget(tokens=token_budget))


def _limiter_for(profile_name: str) -> ProfileRateLimiter:
    with _limiters_lock:
        limiter = _limiters.get(profile_name)
        if limiter is None:
            limiter = ProfileRateLimiter()
            _limiters[profile_name] = limiter
        return limiter


def reset_profile_rate_limiters() -> None:
    with _limiters_lock:
        _limiters.clear()
