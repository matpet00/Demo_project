import os

from quart import Quart, jsonify

from decorators import correlation_id
from logging_setup import configure_logging, log_event
from mail import from_env as mail_from_env
from rabbitmq import RabbitBroker, connect_broker
from servicenow import from_env as snow_from_env
from store import connect_from_env


def create_app(store=None, broker=None, snow=None, mailer=None):
    app = Quart(__name__)
    app.store = store
    app.broker = broker
    app.snow = snow
    app.mailer = mailer

    @app.before_serving
    async def startup():
        configure_logging(os.environ.get("SERVICE_NAME", "delivery"))
        if app.store is None:
            app.store = await connect_from_env()
        if app.snow is None:
            app.snow = snow_from_env()
        if app.mailer is None:
            app.mailer = mail_from_env()
        if app.broker is None:
            app.broker = await connect_broker()
        if isinstance(app.broker, RabbitBroker):
            app.add_background_task(app.broker.consume, _handler(app))

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

    return app


def _handler(app):
    async def handle(payload):
        await handle_message(payload, app.snow, app.mailer, app.store)

    return handle


async def handle_message(message, snow, mailer, store):
    token = correlation_id.set(message.get("correlation_id"))
    try:
        log_event(
            "consumed",
            correlation_id=message.get("correlation_id"),
            message_id=message.get("id"),
            user_uuid=message.get("user_uuid"),
            queue=message.get("queue_name"),
        )
        if message.get("queue_name") == "events.spam":
            await mailer.send_spammer_notice(
                username=message.get("username"),
                user_uuid=message["user_uuid"],
                event_id=message["id"],
            )
            await store.update_message(message["id"], status="not_processed")
            log_event(
                "not_processed",
                correlation_id=message.get("correlation_id"),
                message_id=message["id"],
                user_uuid=message.get("user_uuid"),
                queue="events.spam",
            )
            return "not_processed"
        if message.get("kind") == "incident":
            number = await snow.push_incident(message)
        else:
            number = await snow.push_event(message)
        await store.update_message(message["id"], status="sent", servicenow_number=number)
        log_event(
            "sent",
            correlation_id=message.get("correlation_id"),
            message_id=message["id"],
            user_uuid=message.get("user_uuid"),
            queue=message.get("queue_name"),
            servicenow_number=number,
        )
        return number
    finally:
        correlation_id.reset(token)


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5001")))
