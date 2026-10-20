# Desk

A caller authenticates at WSO2 API Manager and posts an event. The gateway adds the user's name and UUID. One async Python service then decides what happens. An AI agent reads the description and assigns priority 1–5, and a blocked user is stopped before any ticket exists. RabbitMQ carries every accepted call as its own message. A second service delivers it to ServiceNow, or sends mail when the user is blocked.

The project is there to show that path with the failure cases left visible. The same description sent ten times becomes ten events. A ServiceNow error is retried and then parked on its own queue, and it is not rewritten as an incident. A blocked user gets an email that the event was not processed. Logs are one JSON line each and show up in Grafana, filterable by user, correlation id, and queue. Users, the API subscription, and the ServiceNow event table are created once by `preparation.py`, so the running services only accept, route, and deliver. The behaviour, payloads, and diagrams are in [FLOW.md](FLOW.md).

## Flow

`POST /events` hits WSO2 API Manager 4.5.0. The mediation reads the authenticated username and sets `X-User-Name` and `X-User-Uuid` (UUID version 5 of that name).

The ingest service on port 5000 checks the UUID against MySQL. A match goes to `events.spam` and the agent does not run. Any other event goes through the agent in `agents.py`. The agent calls `lookup_service` and `classify_priority`, stores the Thought / Action / Observation trace, and publishes to `priority1_queue` … `priority5_queue`.

The delivery service on port 5001 consumes with `aio_pika`. Each accepted POST is its own message, so the same description sent ten times is sent to ServiceNow ten times. A failed ServiceNow call is retried and then moved to `failed_snow_delivery`. The spam consumer does not call ServiceNow. It sends mail: spammer detected, event was not processed, plus the username and UUID.

Both HTTP services are Quart, so the request and the broker share one asyncio loop.

## Run

```bash
cp .env.example .env
python build.py
```

`build.py` runs the unit tests, builds every image, starts Compose, checks the three priorities and the spammer mail, then waits until preparation finishes. `python preparation.py` on its own creates the WSO2 users, the API subscription, the ServiceNow event table, and bob's spammer row.

Then open:

- Desk: http://localhost:8080
- Mailpit: http://localhost:8025
- Grafana: http://localhost:3000 (anonymous admin, Loki is already attached)
- RabbitMQ: http://localhost:15672 (`events` / `events`)
- WSO2 Publisher: https://localhost:9443/publisher (`admin` / `admin`)

`docker compose up` also starts WSO2, which needs about 4 GB of RAM and a few minutes. After it is healthy, the `provision` container creates users `alice` and `bob`, publishes `POST /events`, and attaches the mediation. Passwords for those two users come from `.env`. The container log prints their UUIDs and the application client id.

Call the gateway with the password grant so the mediation can see the username:

```bash
curl -k -u "$CLIENT_ID:$CLIENT_SECRET" \
  -d "grant_type=password&username=alice&password=$WSO2_ALICE_PASSWORD" \
  https://localhost:9443/oauth2/token
```

```bash
curl -k -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"description":"checkout-api is down"}' \
  https://localhost:8243/desk/v1/events
```

`make demo` talks to ingest directly with the same UUIDs the mediation would set. It sends three descriptions and one event from `bob` after blocking that UUID. The desk shows the trace and the queue. Mailpit shows the spammer mail.

## Model

`OpenAICompatibleBrain` calls OpenRouter model `poolside/laguna-xs-2.1:free`. Put the key in `OPENROUTER_API_KEY`. It is not committed. If the key is missing or the free quota fails, `ScriptedBrain` runs the same loop from the wording of the description.

## ServiceNow

Set `SERVICENOW_BASE_URL`, `SERVICENOW_USER`, and `SERVICENOW_PASSWORD` in `.env`. Events go to `/api/global/em/jsonv2`, then to the `em_event` table. This instance has neither, so `python preparation.py` creates `u_itil_event` and `POST /api/now/desk_events/event`. A failed event stays an event: it is retried and then parked on `failed_snow_delivery`. Without those variables the delivery service uses an in-memory mock (`EM0010001`, `INC0010001`).

## Logs

`logging_setup.py` writes one JSON object per line to stdout: time, service, event, correlation id, message id, user UUID, and queue. Passwords are dropped. Alloy ships container stdout to Loki. In Grafana, filter `{service=~"ingest|delivery"}` or search a `correlation_id`.

## Tests

```bash
python -m pip install -e ".[test]"
make test
```

The tests do not start WSO2, MySQL, or Grafana. They use an in-memory store and broker.

## Layout

`api.py` and `delivery.py` are the two processes. `agents.py` is the loop. `rabbitmq.py` is `aio_pika`. `servicenow.py` only sends events. `mail.py`, `store.py`, `decorators.py`, and `logging_setup.py` are the rest of the runtime. `preparation.py` creates the WSO2 users, the API subscription, the ServiceNow event table, and bob's spammer row. `@idempotent` is implemented and tested, and it is not wrapped around event delivery: a repeated payload is a new event.
