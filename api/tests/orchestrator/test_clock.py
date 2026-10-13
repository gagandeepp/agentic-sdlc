import asyncio

import pytest

from app.orchestrator.clock import ActiveClock


async def test_time_spent_paused_does_not_count() -> None:
    clock = ActiveClock(0.3)
    async with clock.running():
        async with clock.paused():
            await asyncio.sleep(0.5)
        await asyncio.sleep(0.1)
    assert 0.09 < clock.used_s() < 0.25


async def test_active_time_past_the_limit_times_out() -> None:
    clock = ActiveClock(0.2)
    with pytest.raises(TimeoutError):
        async with clock.running():
            await asyncio.sleep(0.1)
            async with clock.paused():
                await asyncio.sleep(0.3)
            await asyncio.sleep(0.2)
    assert clock.used_s() == pytest.approx(0.2, abs=0.05)


async def test_time_used_before_a_restart_carries_over() -> None:
    clock = ActiveClock(1.0, used_s=0.95)
    with pytest.raises(TimeoutError):
        async with clock.running():
            await asyncio.sleep(0.2)
