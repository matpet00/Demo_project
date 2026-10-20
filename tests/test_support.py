import json
import logging

from logging_setup import JsonFormatter
from rabbitmq import queues_from_env
from wso2 import user_uuid, with_user_policy
import uuid


def test_queue_selection():
    assert queues_from_env("1") == ["events.1"]
    assert queues_from_env("1,spam,incidents") == ["events.1", "events.spam", "incidents"]
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
        "queue": "events.1",
    }
    payload = json.loads(JsonFormatter("ingest").format(record))
    assert payload["service"] == "ingest"
    assert payload["event"] == "published"
    assert payload["correlation_id"] == "abc"
    assert "password" not in payload
