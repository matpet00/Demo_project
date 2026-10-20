import os

from quart import Quart, jsonify

from decorators import correlation_id
from logging_setup import configure_logging, log_message
from mail import from_env as mail_from_env
from rabbitmq import FAILED_SNOW, SPAM, RabbitBroker, connect_broker
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
            app.add_background_task(app.broker.consume, _handler(app), _exhausted(app))

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


def _exhausted(app):
    async def mark(payload):
        if payload.get("queue_name") == SPAM:
            return
        await app.store.update_message(payload["id"], status=FAILED_SNOW)

    return mark


async def handle_message(message, snow, mailer, store):
    token = correlation_id.set(message.get("correlation_id"))
    try:
        log_message("consumed", message)
        if message.get("queue_name") == SPAM:
            await mailer.send_spammer_notice(
                username=message.get("username"),
                user_uuid=message["user_uuid"],
                event_id=message["id"],
            )
            await store.update_message(message["id"], status="not_processed")
            log_message("not_processed", message)
            return "not_processed"
        if message.get("kind") == "incident":
            number = await snow.push_incident(message)
        else:
            number = await snow.push_event(message)
        await store.update_message(message["id"], status="sent", servicenow_number=number)
        log_message("sent", message, servicenow_number=number)
        return number
    finally:
        correlation_id.reset(token)


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5001")))
