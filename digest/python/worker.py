"""The activity that stays in Python, on the `python` task queue.

fulltext: trafilatura stays in Python (docs/2026-09-23-fulltext-extractor-fork.md), so this worker
runs fulltext._collect_isolated (this package's fork of the newsroom's): its child process and
SIGKILL are the bound. The TypeScript workflow calls it by name and does the planning and storing.
"""

import asyncio
import os
import signal
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from temporalio import activity
from temporalio.client import Client
from temporalio.worker import Worker

import fulltext
import settings

TASK_QUEUE = "python"
# The image's HEALTHCHECK passes while this file is under a minute old, so Kamal counts a new worker
# deployed only once it polls.
ALIVE_FILE = Path("/tmp/worker-alive")


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


async def touch_while_running(worker: Worker, path: Path = ALIVE_FILE, every_s: float = 5.0) -> None:
    while True:
        if worker.is_running:
            path.touch()
        await asyncio.sleep(every_s)


async def main() -> None:
    # systemd stops the unit with SIGTERM: shut down and exit 0, or every deploy mails a failure.
    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(sig, stop.set)
    client = await Client.connect(os.environ.get("TEMPORAL_ADDRESS", "localhost:7233"), namespace=namespace())
    with ThreadPoolExecutor(max_workers=2) as pool:
        activities = [fetch_fulltext]
        async with Worker(client, task_queue=TASK_QUEUE, activities=activities, activity_executor=pool) as w:
            touching = asyncio.create_task(touch_while_running(w))
            await stop.wait()
            touching.cancel()


if __name__ == "__main__":
    asyncio.run(main())
