"""The activities that stay in Python, on the `python` task queue.

fulltext: trafilatura stays in Python (docs/2026-09-23-fulltext-extractor-fork.md), so this worker
runs the production fetch, fulltext._collect_isolated, unchanged: its child process and SIGKILL are
the bound.

gnews: googlenewsdecoder has no TypeScript equivalent that reports a 429, so the decode is
newsroom's gnews.resolve, paced and bounded as digest._resolve_gnews_links drives it.

The TypeScript workflow calls each activity by name and does the planning and storing.
"""

import asyncio
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from temporalio import activity
from temporalio.client import Client
from temporalio.worker import Worker

import config
import fulltext
import gnews

TASK_QUEUE = "python"


@activity.defn(name="fetchFulltext")  # the name the TypeScript workflow calls
def fetch_fulltext(tasks: list[list[str]]) -> dict:
    results, outcome = fulltext._collect_isolated(
        [(aid, url) for aid, url in tasks],
        max_chars=config.FULLTEXT_MAX_CHARS,
        deadline_s=config.FULLTEXT_DEADLINE_S,
        max_doc_chars=config.FULLTEXT_MAX_DOC_CHARS,
    )
    return {"tasks": len(tasks), "results": results, "outcome": outcome}


_PASS_LOCK = threading.Lock()


@activity.defn(name="decodeLinks")  # the name the TypeScript workflow calls
def decode_links(urls: list[str]) -> dict:
    # One pass at a time per process: gnews's cache and tally are module globals, and Google's
    # budget is per IP, so two runs decoding at once would corrupt both and double the rate.
    while not _PASS_LOCK.acquire(timeout=5):
        activity.heartbeat()
        if activity.is_cancelled():
            return {"links": len(urls), "decoded": {}, "attempted": 0, "outcome": "cancelled"}
    try:
        return _decode_pass(urls)
    finally:
        _PASS_LOCK.release()


def _decode_pass(urls: list[str]) -> dict:
    # gnews keeps its cache and tally at module level, sized for one run per process; this worker
    # serves every run, so each pass starts from nothing, or one day's failed token stays failed.
    with gnews._cache_lock:
        gnews._cache.clear()
    gnews.reset_resolution_stats()
    decoded: dict[str, str] = {}
    outcome = "completed"
    end = time.monotonic() + config.GNEWS_RESOLVE_DEADLINE_S
    for url in urls:
        # A timed-out activity learns it at a heartbeat; its thread is not killed, so it stops here.
        activity.heartbeat()
        if activity.is_cancelled():
            outcome = "cancelled"
            break
        if time.monotonic() > end:
            outcome = "deadline"
            break
        try:
            resolved = gnews.resolve(url, timeout=config.GNEWS_RESOLVE_TIMEOUT_S, delay=config.GNEWS_RESOLVE_DELAY_S)
        except gnews.GnewsRateLimited:
            outcome = "rate_limited"
            break
        if resolved:
            decoded[url] = resolved
    attempted, _ = gnews.resolution_stats()
    return {"links": len(urls), "decoded": decoded, "attempted": attempted, "outcome": outcome}


def namespace() -> str:
    # Production sets the repo's own namespace; the local dev server only has "default".
    return os.environ.get("TEMPORAL_NAMESPACE", "default")


async def main() -> None:
    client = await Client.connect(os.environ.get("TEMPORAL_ADDRESS", "localhost:7233"), namespace=namespace())
    with ThreadPoolExecutor(max_workers=2) as pool:
        activities = [fetch_fulltext, decode_links]
        await Worker(client, task_queue=TASK_QUEUE, activities=activities, activity_executor=pool).run()


if __name__ == "__main__":
    asyncio.run(main())
