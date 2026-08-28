import argparse
import time

from .config import configure_utf8_stdio, ensure_runtime_dirs
from .db import initialize_business_database
from .task_queue_service import (
    DEFAULT_JOB_LEASE_SECONDS,
    default_worker_id,
    recover_expired_jobs,
    run_one_job,
)


def run_worker(
    *,
    once: bool = False,
    poll_interval: float = 1.0,
    lease_seconds: int = DEFAULT_JOB_LEASE_SECONDS,
    retry_delay_seconds: int = 5,
) -> int:
    worker_id = default_worker_id()
    processed = 0
    while True:
        recover_expired_jobs()
        job = run_one_job(
            worker_id,
            lease_seconds=lease_seconds,
            retry_delay_seconds=retry_delay_seconds,
        )
        if job is not None:
            processed += 1
            continue
        if once:
            return processed
        time.sleep(poll_interval)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Paper Hunter task worker.")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Process all currently ready jobs and exit.",
    )
    parser.add_argument("--poll-interval", type=float, default=1.0)
    parser.add_argument(
        "--lease-seconds",
        type=int,
        default=DEFAULT_JOB_LEASE_SECONDS,
    )
    parser.add_argument("--retry-delay-seconds", type=int, default=5)
    args = parser.parse_args()
    if args.poll_interval <= 0:
        parser.error("--poll-interval must be greater than 0")
    if args.lease_seconds < 2:
        parser.error("--lease-seconds must be at least 2")
    if args.retry_delay_seconds < 0:
        parser.error("--retry-delay-seconds cannot be negative")

    configure_utf8_stdio()
    ensure_runtime_dirs()
    initialize_business_database()
    try:
        return run_worker(
            once=args.once,
            poll_interval=args.poll_interval,
            lease_seconds=args.lease_seconds,
            retry_delay_seconds=args.retry_delay_seconds,
        )
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
