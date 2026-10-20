import json
import logging

from logging_setup import JsonFormatter
from rabbitmq import FAILED_SNOW, MemoryBroker, _deliver, queues_from_env
from preparation import user_uuid, with_user_policy
import uuid


class _FailedMessage:
    def __init__(self, payload, headers=None):
        self.body = json.dumps(payload).encode()
        self.headers = headers or {}

    def process(self, requeue=False, ignore_processed=True):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


async def test_snow_failure_lands_in_failed_snow_delivery():
    broker = MemoryBroker()
    payload = {"id": "evt-1", "queue_name": "priority1_queue", "correlation_id": "corr"}
    marked = []

    async def fail(_payload):
        raise RuntimeError("snow down")

    async def exhausted(item):
        marked.append(item["id"])

    await _deliver(
        broker,
        _FailedMessage(payload, {"x-attempts": 2}),
        fail,
        exhausted,
    )
    assert broker.queues[FAILED_SNOW][0]["id"] == "evt-1"
    assert marked == ["evt-1"]
    assert broker.queues["priority1_queue"] == []


def test_queue_selection():
    assert queues_from_env("1") == ["priority1_queue"]
    assert queues_from_env("1,spam,incidents") == ["priority1_queue", "events.spam", "incidents"]
    assert len(queues_from_env("all")) == 7


def test_user_uuid_is_stable_and_distinct():
    assert user_uuid("alice") == str(uuid.uuid5(uuid.UUID("6ba7b811-9dad-11d1-80b4-00c04fd430c8"), "alice"))
    assert user_uuid("alice") == user_uuid("alice")
    assert user_uuid("alice") != user_uuid("bob")


def test_policy_is_attached_only_to_the_data_routes():
    api = {
        "operations": [
            {"verb": "POST", "target": "/events"},
            {"verb": "POST", "target": "/incidents"},
            {"verb": "POST", "target": "/add_spammer"},
        ]
    }
    updated = with_user_policy(api)
    assert updated["operations"][0]["operationPolicies"]["request"][0]["policyName"] == "userUuid"
    assert updated["operations"][1]["authType"] == "Application & Application User"
    assert "operationPolicies" not in updated["operations"][2]


def test_json_log_drops_secrets():
    record = logging.LogRecord("desk", logging.INFO, __file__, 1, "published", (), None)
    record.desk = {
        "event": "published",
        "correlation_id": "abc",
        "password": "nope",
        "queue": "priority1_queue",
    }
    payload = json.loads(JsonFormatter("ingest").format(record))
    assert payload["service"] == "ingest"
    assert payload["event"] == "published"
    assert payload["correlation_id"] == "abc"
    assert "password" not in payload
