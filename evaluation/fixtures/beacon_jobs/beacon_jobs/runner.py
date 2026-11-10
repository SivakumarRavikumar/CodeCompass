"""Small synchronous job runner for the synthetic Beacon fixture."""

from collections.abc import Callable

from beacon_jobs.retry import RetryPolicy


def run_job(action: Callable[[], str], policy: RetryPolicy) -> str:
    """Run an action until success or the configured attempt limit."""
    last_error: Exception | None = None
    for attempt in range(1, policy.max_attempts + 1):
        try:
            return action()
        except RuntimeError as error:
            last_error = error
            if attempt < policy.max_attempts:
                policy.delay_for(attempt)
    raise RuntimeError("job failed after retries") from last_error
