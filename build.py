#!/usr/bin/env python3
"""Build the stack and test it.

    python build.py
"""

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
INGEST = "http://127.0.0.1:5000"
DELIVERY = "http://127.0.0.1:5001"


def run(command, *, check=True):
    print("+", " ".join(command), flush=True)
    result = subprocess.run(command, cwd=ROOT)
    if check and result.returncode != 0:
        raise SystemExit(result.returncode)
    return result.returncode


def load_env():
    path = ROOT / ".env"
    if not path.exists():
        raise SystemExit("Missing .env. Copy .env.example and fill it in.")
    values = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def request(method, url, payload=None, headers=None, timeout=10):
    data = None if payload is None else json.dumps(payload).encode()
    req_headers = {"Accept": "application/json"}
    if payload is not None:
        req_headers["Content-Type"] = "application/json"
    if headers:
        req_headers.update(headers)
    request_ = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(request_, timeout=timeout) as response:
            raw = response.read().decode()
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        raise RuntimeError(f"{method} {url} -> {exc.code} {raw}") from exc


def wait_for(label, probe, timeout):
    deadline = time.time() + timeout
    last = "not ready"
    while time.time() < deadline:
        try:
            if probe():
                print(f"ok  {label}", flush=True)
                return
        except Exception as exc:
            last = str(exc)
        time.sleep(2)
    raise SystemExit(f"Timed out waiting for {label}: {last}")


def healthy(url):
    status, body = request("GET", url, timeout=3)
    return status == 200 and body.get("status") in {"ok", "ready", None}


def mail_count():
    status, body = request("GET", "http://127.0.0.1:8025/api/v1/messages", timeout=3)
    if status != 200:
        return 0
    return int(body.get("total", body.get("count", len(body.get("messages") or []))))


def wait_status(message_id, expected):
    deadline = time.time() + 90
    last = None
    while time.time() < deadline:
        _, body = request("GET", f"{INGEST}/events/{message_id}")
        last = body.get("status")
        if last == expected:
            return body
        time.sleep(1)
    run(["docker", "compose", "logs", "--tail", "60", "delivery"], check=False)
    raise SystemExit(f"{message_id} stayed {last}, expected {expected}")


def smoke(env):
    from wso2 import user_uuid

    alice = user_uuid("alice")
    bob = user_uuid("bob")
    api_key = env.get("API_KEY", "")
    headers = {"X-API-Key": api_key} if api_key else {}
    _, _ = request(
        "POST",
        f"{INGEST}/add_spammer",
        {"uuid": bob, "username": "bob"},
        headers,
    )
    before = mail_count()
    _, outage = request(
        "POST",
        f"{INGEST}/events",
        {"description": "checkout-api outage, payments are down"},
        {"X-User-Uuid": alice, "X-User-Name": "alice"},
    )
    _, slow = request(
        "POST",
        f"{INGEST}/events",
        {"description": "edge-proxy latency is high and checkout is slow"},
        {"X-User-Uuid": alice, "X-User-Name": "alice"},
    )
    _, info = request(
        "POST",
        f"{INGEST}/events",
        {"description": "nightly report finished, informational notice"},
        {"X-User-Uuid": alice, "X-User-Name": "alice"},
    )
    _, spam = request(
        "POST",
        f"{INGEST}/events",
        {"description": "checkout-api outage, payments are down"},
        {"X-User-Uuid": bob, "X-User-Name": "bob"},
    )
    if [outage["queue_name"], slow["queue_name"], info["queue_name"], spam["queue_name"]] != [
        "events.1",
        "events.2",
        "events.5",
        "events.spam",
    ]:
        raise SystemExit(f"Unexpected queues: {outage} {slow} {info} {spam}")
    if not outage["trace"]:
        raise SystemExit("ReAct trace was empty")
    for message_id in (outage["id"], slow["id"], info["id"]):
        done = wait_status(message_id, "sent")
        print(f"ok  sent {done.get('servicenow_number')} {message_id}", flush=True)
    wait_status(spam["id"], "not_processed")
    deadline = time.time() + 30
    while time.time() < deadline:
        if mail_count() > before:
            print("ok  spammer mail", flush=True)
            return
        time.sleep(1)
    raise SystemExit("Mailpit did not receive the spammer notice")


def main():
    env = load_env()
    run([sys.executable, "-m", "pytest", "-q"])
    run(["docker", "compose", "build"])
    run(["docker", "compose", "up", "-d"])
    wait_for("ingest", lambda: healthy(f"{INGEST}/readyz"), 180)
    wait_for("delivery", lambda: healthy(f"{DELIVERY}/readyz"), 180)
    wait_for("grafana", lambda: request("GET", "http://127.0.0.1:3000/api/health", timeout=3)[0] == 200, 180)
    smoke(env)
    print("+ docker compose wait provision", flush=True)
    code = run(["docker", "compose", "wait", "provision"], check=False)
    if code != 0:
        run(["docker", "compose", "logs", "--tail", "80", "provision", "wso2"], check=False)
        raise SystemExit(code)
    print("ok  wso2 provision", flush=True)
    print("Desk http://localhost:5000")
    print("Mailpit http://localhost:8025")
    print("Grafana http://localhost:3000")


if __name__ == "__main__":
    main()
