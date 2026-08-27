import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import store as store_mod  # noqa: E402
from server.store import Store  # noqa: E402


@pytest.fixture
def store():
    s = Store(":memory:")
    store_mod.set_store(s)
    yield s
    store_mod.set_store(None)
    s.close()


@pytest.fixture
def client(store):
    from fastapi.testclient import TestClient

    from server.main import app

    with TestClient(app) as c:
        yield c


def drive(game, actions, *, seed="testseed", mode="", opts=None):
    """Run a list of actions through a game and collect (state, events, result)."""
    state = game.new_state(seed=seed, mode=mode, opts=opts or {})
    events, result = [], None

    async def run():
        nonlocal result
        for action in actions:
            act = action(state) if callable(action) else action
            if act is None:
                continue
            async for ev in game.act(state, act):
                events.append(ev)
                if ev.event == "end":
                    result = ev.data["result"]

    asyncio.run(run())
    return state, events, result


def kinds(events, name):
    return [e.data for e in events if e.event == name]
