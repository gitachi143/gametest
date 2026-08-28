import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Every test runs against the scripted opponent and an in-memory database, so
# the suite needs no credentials and leaves nothing behind.
os.environ["LLM_PROVIDER"] = "mock"
os.environ["DB_PATH"] = ":memory:"
os.environ["APP_SECRET"] = "test-secret"

import pytest

from app.store import Store, set_store


@pytest.fixture(autouse=True)
def fresh_store():
    store = Store(":memory:")
    set_store(store)
    yield store
    set_store(None)
    store.close()


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c
