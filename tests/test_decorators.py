import pytest

from decorators import clear_idempotent_cache, idempotent, retry


@pytest.fixture(autouse=True)
def _clean_cache():
    clear_idempotent_cache()
    yield
    clear_idempotent_cache()


async def test_retry_eventually_succeeds():
    state = {"n": 0}

    @retry(times=3, delay=0, exceptions=(ValueError,))
    async def flaky():
        state["n"] += 1
        if state["n"] < 3:
            raise ValueError("not yet")
        return "ok"

    assert await flaky() == "ok"
    assert state["n"] == 3


async def test_idempotent_runs_once():
    state = {"n": 0}

    @idempotent(lambda value: value)
    async def double(value):
        state["n"] += 1
        return value * 2

    assert await double(2) == 4
    assert await double(2) == 4
    assert state["n"] == 1
    assert await double(3) == 6
    assert state["n"] == 2
