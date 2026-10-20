import os
import uuid
from datetime import datetime, timezone

from quart import Quart, abort, jsonify, render_template, request

from agents import make_brain, run_react
from decorators import correlation_id, require_api_key
from logging_setup import configure_logging, log_event, log_message
from rabbitmq import SPAM, connect_broker, priority_queue
from store import connect_from_env


class AuthError(Exception):
    pass


def create_app(store=None, broker=None, brain_factory=None):
    app = Quart(__name__, template_folder="templates")
    app.store = store
    app.broker = broker
    app.brain_factory = brain_factory or make_brain

    @app.before_serving
    async def startup():
        configure_logging(os.environ.get("SERVICE_NAME", "ingest"))
        if app.store is None:
            app.store = await connect_from_env()
        if app.broker is None:
            app.broker = await connect_broker()

    @app.get("/healthz")
    async def healthz():
        return jsonify({"status": "ok"})

    @app.get("/readyz")
    async def readyz():
        try:
            await app.store.ping()
        except Exception:
            return jsonify({"status": "down"}), 503
        return jsonify({"status": "ready"})

    @app.get("/")
    async def desk():
        return await render_template("desk.html")

    @app.get("/favicon.ico")
    async def favicon():
        return "", 204

    @app.get("/board")
    async def board():
        return jsonify(
            {
                "messages": await app.store.list_messages(),
                "spammers": await app.store.list_spammers(),
            }
        )

    @app.get("/events/<message_id>")
    async def one_event(message_id):
        message = await app.store.get_message(message_id)
        if message is None:
            abort(404)
        return jsonify(message)

    @app.post("/events")
    async def events():
        return await _ingest(app, "event")

    @app.post("/add_spammer")
    @require_api_key
    async def add_spammer():
        user, username = await _spammer_body()
        await app.store.add_spammer(user, username)
        log_event("spammer_added", user_uuid=user)
        return jsonify({"uuid": user, "username": username}), 201

    @app.post("/remove_spammer")
    @require_api_key
    async def remove_spammer():
        user, _username = await _spammer_body()
        await app.store.remove_spammer(user)
        log_event("spammer_removed", user_uuid=user)
        return jsonify({"uuid": user})

    @app.get("/list_spammers")
    @require_api_key
    async def list_spammers():
        return jsonify({"spammers": await app.store.list_spammers()})

    return app


async def _json():
    try:
        body = await request.get_json(silent=True)
    except TypeError:
        body = await request.get_json()
    return body or {}


async def _spammer_body():
    body = await _json()
    user = (body.get("uuid") or "").strip()
    if not user:
        abort(400)
    return user, body.get("username")


async def _ingest(app, kind):
    user = request.headers.get("X-User-Uuid", "").strip()
    username = request.headers.get("X-User-Name", "").strip()
    body = await _json()
    description = (body.get("description") or body.get("short_description") or "").strip()
    correlation = request.headers.get("X-Correlation-Id") or str(uuid.uuid4())
    token = correlation_id.set(correlation)
    try:
        message = await accept_message(
            kind=kind,
            user_uuid=user,
            username=username,
            description=description,
            correlation=correlation,
            store=app.store,
            broker=app.broker,
            brain_factory=app.brain_factory,
        )
    except AuthError:
        abort(401)
    except ValueError:
        abort(400)
    finally:
        correlation_id.reset(token)
    return jsonify(message), 202


async def accept_message(
    *,
    kind,
    user_uuid,
    username,
    description,
    correlation,
    store,
    broker,
    brain_factory,
):
    if not user_uuid:
        raise AuthError()
    if not description:
        raise ValueError("description is required")
    message = {
        "id": str(uuid.uuid4()),
        "kind": kind,
        "user_uuid": user_uuid,
        "username": username,
        "description": description,
        "priority": None,
        "queue_name": "",
        "status": "queued",
        "trace": [],
        "correlation_id": correlation,
        "servicenow_number": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    log_message("received", message, kind=kind)
    if await store.is_spammer(user_uuid):
        message["queue_name"] = SPAM
        log_message("spammer", message)
    else:
        result = await run_react(description, brain_factory())
        for step in result["trace"]:
            log_message(
                "react_step",
                message,
                thought=step.get("thought"),
                action=step.get("action"),
            )
        message["priority"] = result["priority"]
        message["trace"] = result["trace"]
        message["queue_name"] = priority_queue(result["priority"])
    await store.save_message(message)
    await broker.publish(message["queue_name"], message)
    log_message("published", message)
    return message


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
