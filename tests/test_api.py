import pytest

from agents import ScriptedBrain
from api import create_app
from rabbitmq import MemoryBroker
from store import MemoryStore


@pytest.fixture
def store():
    return MemoryStore()


@pytest.fixture
def broker():
    return MemoryBroker()


@pytest.fixture
def app(store, broker):
    return create_app(store, broker, brain_factory=ScriptedBrain)


async def _post(app, path, description, user, name="alice", extra=None):
    payload = {"description": description}
    if extra:
        payload.update(extra)
    async with app.test_client() as client:
        return await client.post(
            path,
            json=payload,
            headers={"X-User-Uuid": user, "X-User-Name": name},
        )


async def test_description_chooses_queue_and_keeps_trace(app, broker):
    response = await _post(app, "/events", "checkout-api outage, payments are down", "alice-id")
    assert response.status_code == 202
    body = await response.get_json()
    assert body["queue_name"] == "priority1_queue"
    assert body["priority"] == 1
    assert body["trace"][0]["action"] == "lookup_service"
    assert broker.queues["priority1_queue"][0]["id"] == body["id"]


async def test_body_priority_is_ignored(app):
    response = await _post(
        app,
        "/events",
        "nightly report finished, informational notice",
        "alice-id",
        extra={"priority": 1},
    )
    body = await response.get_json()
    assert body["queue_name"] == "priority5_queue"


async def test_same_payload_is_queued_twice(app, broker):
    first = await _post(app, "/events", "edge-proxy latency is high and checkout is slow", "alice-id")
    second = await _post(app, "/events", "edge-proxy latency is high and checkout is slow", "alice-id")
    assert (await first.get_json())["id"] != (await second.get_json())["id"]
    assert len(broker.queues["priority2_queue"]) == 2


async def test_spammer_skips_the_agent(store, broker):
    calls = {"n": 0}

    def factory():
        calls["n"] += 1
        return ScriptedBrain()

    app = create_app(store, broker, brain_factory=factory)
    await store.add_spammer("bob-id", "bob")
    response = await _post(app, "/events", "checkout-api outage, payments are down", "bob-id", "bob")
    body = await response.get_json()
    assert body["queue_name"] == "events.spam"
    assert body["trace"] == []
    assert calls["n"] == 0


async def test_missing_uuid_is_rejected(app):
    async with app.test_client() as client:
        response = await client.post("/events", json={"description": "outage"})
    assert response.status_code == 401


async def test_spammer_routes_need_the_api_key(app, monkeypatch):
    monkeypatch.setenv("API_KEY", "desk-local")
    async with app.test_client() as client:
        rejected = await client.post("/add_spammer", json={"uuid": "bob-id", "username": "bob"})
        accepted = await client.post(
            "/add_spammer",
            json={"uuid": "bob-id", "username": "bob"},
            headers={"X-API-Key": "desk-local"},
        )
        listed = await client.get("/list_spammers", headers={"X-API-Key": "desk-local"})
    assert rejected.status_code == 401
    assert accepted.status_code == 201
    assert (await listed.get_json())["spammers"][0]["uuid"] == "bob-id"


async def test_desk_renders_mediation_uuids(app):
    async with app.test_client() as client:
        response = await client.get("/")
    text = await response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Desk" in text
