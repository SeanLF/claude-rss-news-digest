"""The activity that stays in Python, on the `python` task queue.

fulltext: trafilatura stays in Python (docs/2026-09-23-fulltext-extractor-fork.md), so this worker
runs the production fetch, fulltext._collect_isolated, unchanged: its child process and SIGKILL are
the bound. The TypeScript workflow calls it by name and does the planning and storing.
"""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor

from temporalio import activity
from temporalio.client import Client
from temporalio.worker import Worker

import config
import fulltext

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


def namespace() -> str:
    # Production sets the repo's own namespace; the local dev server only has "default".
    return os.environ.get("TEMPORAL_NAMESPACE", "default")


async def main() -> None:
    client = await Client.connect(os.environ.get("TEMPORAL_ADDRESS", "localhost:7233"), namespace=namespace())
    with ThreadPoolExecutor(max_workers=2) as pool:
        activities = [fetch_fulltext]
        await Worker(client, task_queue=TASK_QUEUE, activities=activities, activity_executor=pool).run()


if __name__ == "__main__":
    asyncio.run(main())
