import json
import os

import httpx

from decorators import retry, traced


def build_event_record(message):
    return {
        "source": "desk",
        "node": message.get("username") or message["user_uuid"],
        "type": message["kind"],
        "resource": "desk",
        "severity": str(message.get("priority") or 3),
        "description": message["description"],
        "message_key": message["id"],
        "additional_info": json.dumps(
            {
                "correlation_id": message["correlation_id"],
                "user_uuid": message["user_uuid"],
            }
        ),
    }


def build_incident_record(message):
    text = message["description"]
    return {
        "short_description": text[:160],
        "description": f"{text}\ncorrelation_id={message['correlation_id']}",
        "urgency": "2",
        "impact": "2",
    }


def _number(payload):
    if not isinstance(payload, dict):
        return None
    result = payload.get("result", payload)
    if isinstance(result, list) and result:
        result = result[0]
    if isinstance(result, dict):
        return result.get("number") or result.get("sys_id")
    return None


class MockServiceNow:
    def __init__(self):
        self.events = []
        self.incidents = []
        self._events = 10000
        self._incidents = 10000

    async def push_event(self, message):
        self._events += 1
        self.events.append(build_event_record(message))
        return f"EM{self._events:07d}"

    async def push_incident(self, message):
        self._incidents += 1
        self.incidents.append(build_incident_record(message))
        return f"INC{self._incidents:07d}"


class LiveServiceNow:
    def __init__(self, base_url, user, password, client=None):
        self.base_url = base_url.rstrip("/")
        self.user = user
        self.password = password
        self.client = client or httpx.AsyncClient(timeout=20)

    async def push_event(self, message):
        record = build_event_record(message)
        response = await self._post("/api/global/em/jsonv2", {"records": [record]})
        if response.status_code in {400, 404, 501}:
            response = await self._post("/api/now/table/em_event", record)
        response.raise_for_status()
        return _number(response.json()) or "accepted"

    async def push_incident(self, message):
        response = await self._post("/api/now/table/incident", build_incident_record(message))
        response.raise_for_status()
        return _number(response.json()) or "accepted"

    @retry(times=3, delay=0.4, exceptions=(httpx.TransportError,))
    @traced
    async def _post(self, path, payload):
        return await self.client.post(
            f"{self.base_url}{path}",
            json=payload,
            auth=(self.user, self.password),
            headers={"Accept": "application/json"},
        )


def from_env():
    base = os.environ.get("SERVICENOW_BASE_URL", "").strip()
    user = os.environ.get("SERVICENOW_USER", "").strip()
    password = os.environ.get("SERVICENOW_PASSWORD", "").strip()
    if base and user and password:
        return LiveServiceNow(base, user, password)
    return MockServiceNow()
