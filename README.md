# Desk

Events come in through WSO2. A ReAct loop reads the description and chooses priority 1–5. RabbitMQ carries the message. A second service sends it to ServiceNow, unless the caller UUID is on the spammer list. Then the event is not sent and an email says so.

## Flow

`POST /events` and `POST /incidents` hit WSO2 API Manager 4.5.0. The mediation reads the authenticated username and sets `X-User-Name` and `X-User-Uuid` (UUID version 5 of that name). Both routes use that same UUID.

The ingest service on port 5000 checks the UUID against MySQL. A match goes to `events.spam` and the agent does not run. Any other event goes through the ReAct loop in `agents.py`. The loop calls `lookup_service` and `classify_priority`, stores the Thought / Action / Observation trace, and publishes to `events.1` … `events.5`. Incidents that are not spam go to `incidents`.

The delivery service on port 5001 consumes with `aio_pika`. Each accepted POST is its own message, so the same description sent ten times is sent to ServiceNow ten times. A failed call is retried and then moved to `events.dlq`. The spam consumer does not call ServiceNow. It sends mail: spammer detected, event was not processed, plus the username and UUID.

Both HTTP services are Quart, so the request and the broker share one asyncio loop.

## Run

```bash
cp .env.example .env
docker compose up --build
```

Then open:

- Desk: http://localhost:5000
- Mailpit: http://localhost:8025
- Grafana: http://localhost:3000 (anonymous admin, Loki is already attached)
- RabbitMQ: http://localhost:15672 (`events` / `events`)
- WSO2 Publisher: https://localhost:9443/publisher (`admin` / `admin`)

`docker compose up` also starts WSO2, which needs about 4 GB of RAM and a few minutes. After it is healthy, the `provision` container creates users `alice` and `bob`, publishes `POST /events` and `POST /incidents`, and attaches the mediation. Passwords for those two users come from `.env`. The container log prints their UUIDs and the application client id.

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

Set `SERVICENOW_BASE_URL`, `SERVICENOW_USER`, and `SERVICENOW_PASSWORD` in `.env`. Events go to `/api/global/em/jsonv2`, and to the `em_event` table if Event Management is not enabled. Incidents go to `/api/now/table/incident`. Without those variables the delivery service uses an in-memory mock (`EM0010001`, `INC0010001`).

## Logs

`logging_setup.py` writes one JSON object per line to stdout: time, service, event, correlation id, message id, user UUID, and queue. Passwords are dropped. Alloy ships container stdout to Loki. In Grafana, filter `{service=~"ingest|delivery"}` or search a `correlation_id`.

## Tests

```bash
python -m pip install -e ".[test]"
make test
```

The tests do not start WSO2, MySQL, or Grafana. They use an in-memory store and broker.

## Layout

`api.py` and `delivery.py` are the two processes. `agents.py` is the loop. `rabbitmq.py` is `aio_pika`. `servicenow.py`, `mail.py`, `store.py`, `wso2.py`, `mediation.xml`, `decorators.py`, and `logging_setup.py` are the rest. `@idempotent` is implemented and tested, and it is not wrapped around event delivery: a repeated payload is a new event.
