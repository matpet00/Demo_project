import json
import os

EXCHANGE = "events"
DLQ = "events.dlq"
WORK_QUEUES = [f"events.{priority}" for priority in range(1, 6)] + ["events.spam", "incidents"]
MAX_ATTEMPTS = 3


def queues_from_env(value=None):
    raw = os.environ.get("QUEUES", "all") if value is None else value
    if not raw or raw.strip() == "all":
        return list(WORK_QUEUES)
    names = []
    for part in raw.split(","):
        part = part.strip()
        if part in {"1", "2", "3", "4", "5"}:
            names.append(f"events.{part}")
        elif part == "spam":
            names.append("events.spam")
        elif part == "incidents":
            names.append("incidents")
        elif part in WORK_QUEUES:
            names.append(part)
        else:
            raise ValueError(f"unknown queue {part}")
    return names


class MemoryBroker:
    def __init__(self):
        self.queues = {name: [] for name in WORK_QUEUES}

    async def publish(self, queue_name, payload):
        self.queues.setdefault(queue_name, []).append(json.loads(json.dumps(payload)))

    async def close(self):
        return None


class RabbitBroker:
    def __init__(self, url):
        self.url = url
        self.connection = None
        self.channel = None
        self.exchange = None

    async def connect(self):
        import aio_pika

        self.connection = await aio_pika.connect_robust(self.url)
        self.channel = await self.connection.channel()
        await self.channel.set_qos(10)
        self.exchange = await self.channel.declare_exchange(
            EXCHANGE,
            aio_pika.ExchangeType.DIRECT,
            durable=True,
        )
        await self.channel.declare_queue(DLQ, durable=True)
        for name in WORK_QUEUES:
            queue = await self.channel.declare_queue(name, durable=True)
            await queue.bind(self.exchange, routing_key=name)

    async def publish(self, queue_name, payload, headers=None):
        import aio_pika

        message = aio_pika.Message(
            body=json.dumps(payload).encode(),
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            correlation_id=str(payload.get("correlation_id") or ""),
            headers=headers or {},
        )
        await self.exchange.publish(message, routing_key=queue_name)

    async def consume(self, handler):
        async def _on_message(message):
            await _deliver(self, message, handler)

        for name in queues_from_env():
            queue = await self.channel.get_queue(name)
            await queue.consume(_on_message)

    async def close(self):
        if self.connection is not None:
            await self.connection.close()


async def connect_broker(url=None):
    broker = RabbitBroker(url or os.environ["RABBIT_URL"])
    await broker.connect()
    return broker


async def _deliver(broker, message, handler):
    from logging_setup import log_event

    async with message.process(requeue=False, ignore_processed=True):
        payload = json.loads(message.body.decode())
        try:
            await handler(payload)
        except Exception as exc:
            headers = dict(message.headers or {})
            attempts = int(headers.get("x-attempts", 0)) + 1
            log_event(
                "retry" if attempts < MAX_ATTEMPTS else "dlq",
                correlation_id=payload.get("correlation_id"),
                message_id=payload.get("id"),
                queue=payload.get("queue_name"),
                error=type(exc).__name__,
                attempt=attempts,
            )
            if attempts >= MAX_ATTEMPTS:
                await broker.publish(DLQ, payload, headers={"x-attempts": attempts})
            else:
                await broker.publish(
                    payload["queue_name"],
                    payload,
                    headers={"x-attempts": attempts},
                )
