from delivery import handle_message
from mail import RecordingMailer
from servicenow import MockServiceNow
from store import MemoryStore


def _message(message_id, queue_name, kind="event", priority=1):
    return {
        "id": message_id,
        "kind": kind,
        "user_uuid": "user-1",
        "username": "alice",
        "description": "checkout-api outage, payments are down",
        "priority": priority,
        "queue_name": queue_name,
        "status": "queued",
        "trace": [],
        "correlation_id": "corr-1",
        "created_at": "2026-10-20T00:00:00+00:00",
    }


async def test_event_is_sent_and_repeated_payloads_stay_distinct():
    store = MemoryStore()
    snow = MockServiceNow()
    first = _message("one", "events.1")
    second = _message("two", "events.1")
    await store.save_message(first)
    await store.save_message(second)
    assert await handle_message(first, snow, RecordingMailer(), store) == "EM0010001"
    assert await handle_message(second, snow, RecordingMailer(), store) == "EM0010002"
    assert snow.events[0]["message_key"] == "one"
    assert snow.events[1]["message_key"] == "two"
    assert (await store.get_message("one"))["status"] == "sent"


async def test_spam_sends_mail_and_skips_servicenow():
    store = MemoryStore()
    snow = MockServiceNow()
    mailer = RecordingMailer()
    message = _message("spam-1", "events.spam")
    message["username"] = "bob"
    message["user_uuid"] = "bob-uuid"
    await store.save_message(message)
    result = await handle_message(message, snow, mailer, store)
    assert result == "not_processed"
    assert snow.events == []
    assert snow.incidents == []
    assert "Spammer detected" in mailer.sent[0]["subject"]
    assert "Event was not processed." in mailer.sent[0]["body"]
    assert "bob-uuid" in mailer.sent[0]["body"]
    assert "bob" in mailer.sent[0]["body"]
    assert (await store.get_message("spam-1"))["status"] == "not_processed"


async def test_incident_uses_the_incident_api():
    store = MemoryStore()
    snow = MockServiceNow()
    message = _message("inc-1", "incidents", kind="incident", priority=None)
    await store.save_message(message)
    assert await handle_message(message, snow, RecordingMailer(), store) == "INC0010001"
    assert snow.events == []
