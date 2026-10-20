"""Wait with a timeout while preserving cancellation of an owning lifecycle task.

Python 3.10's asyncio.wait_for can lose cancellation when its child completes in
that same event-loop turn. Explicit wait/cleanup keeps Stop effective in that race.
"""

import asyncio


async def wait_with_timeout(awaitable, timeout):
    task = asyncio.ensure_future(awaitable)
    try:
        done, _ = await asyncio.wait({task}, timeout=timeout)
        if not done:
            raise asyncio.TimeoutError()
        return task.result()
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
