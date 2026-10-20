"""Cancellation wins even when an owned operation completes in the same loop turn."""

import asyncio

import pytest

from packet_radio.runtime.deadline import wait_with_timeout


@pytest.mark.parametrize("failure", [False, True])
def test_cancel_racing_completed_operation_is_not_swallowed(failure):
    async def exercise():
        async def completes():
            asyncio.get_running_loop().call_soon(owner.cancel)
            if failure:
                raise OSError("completed request failed")
            return "completed"

        owner = asyncio.create_task(wait_with_timeout(completes(), 1))
        with pytest.raises(asyncio.CancelledError):
            await owner

    asyncio.run(exercise())


def test_timeout_and_cancellation_reap_pending_operation():
    async def exercise():
        for cancel in (False, True):
            entered, cleaned = asyncio.Event(), asyncio.Event()

            async def operation():
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cleaned.set()

            owner = asyncio.create_task(wait_with_timeout(operation(), 0.02 if not cancel else 1))
            await entered.wait()
            if cancel:
                owner.cancel()
            with pytest.raises(asyncio.CancelledError if cancel else asyncio.TimeoutError):
                await owner
            assert cleaned.is_set()

    asyncio.run(exercise())


def test_completed_result_and_error_are_preserved():
    async def exercise():
        async def operation(failure=False):
            if failure:
                raise ValueError("operation error")
            return 42

        assert await wait_with_timeout(operation(), 1) == 42
        with pytest.raises(ValueError, match="operation error"):
            await wait_with_timeout(operation(True), 1)

    asyncio.run(exercise())
