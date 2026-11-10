"""Retry policy for the synthetic Beacon fixture."""

from dataclasses import dataclass


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay_seconds: float = 0.25

    def delay_for(self, attempt: int) -> float:
        """Return exponential delay for a one-based attempt number."""
        if attempt < 1 or attempt > self.max_attempts:
            raise ValueError("attempt is outside the retry policy")
        return self.base_delay_seconds * (2 ** (attempt - 1))
