"""The activity that stays in Python, on the `python` task queue.

fulltext: trafilatura stays in Python (docs/2026-09-23-fulltext-extractor-fork.md), so this worker
runs fulltext._collect_isolated (this package's fork of the newsroom's): its child process and
SIGKILL are the bound. The TypeScript workflow calls it by name and does the planning and storing.
"""

import asyncio
import os
import signal
from concurrent.futures import ThreadPoolExecutor

from temporalio import activity
from temporalio.client import Client
from temporalio.worker import Worker

import fulltext
import settings

TASK_QUEUE = "python"


@activity.defn(name="fetchFulltext")  # the name the TypeScript workflow calls
def fetch_fulltext(tasks: list[list[str]]) -> dict:
    results, outcome = fulltext._collect_isolated(
        [(aid, url) for aid, url in tasks],
        max_chars=settings.FULLTEXT_MAX_CHARS,
        deadline_s=settings.FULLTEXT_DEADLINE_S,
        max_doc_chars=settings.FULLTEXT_MAX_DOC_CHARS,
    )
    return {"tasks": len(tasks), "results": results, "outcome": outcome}


def namespace() -> str:
    # Production sets the repo's own namespace; the local dev server only has "default".
    return os.environ.get("TEMPORAL_NAMESPACE", "default")


async def main() -> None:
    # systemd stops the unit with SIGTERM: shut down and exit 0, or every deploy mails a failure.
    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(sig, stop.set)
    client = await Client.connect(os.environ.get("TEMPORAL_ADDRESS", "localhost:7233"), namespace=namespace())
    with ThreadPoolExecutor(max_workers=2) as pool:
        activities = [fetch_fulltext]
        async with Worker(client, task_queue=TASK_QUEUE, activities=activities, activity_executor=pool):
            await stop.wait()


if __name__ == "__main__":
    asyncio.run(main())
