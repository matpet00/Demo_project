import json
import os

EXCHANGE = "events"
DLQ = "events.dlq"
FAILED_SNOW = "failed_snow_delivery"
SPAM = "events.spam"


def priority_queue(priority):
    return f"priority{priority}_queue"


WORK_QUEUES = [priority_queue(priority) for priority in range(1, 6)] + [SPAM]
MAX_ATTEMPTS = 3


def queues_from_env(value=None):
    raw = os.environ.get("QUEUES", "all") if value is None else value
    if not raw or raw.strip() == "all":
        return list(WORK_QUEUES)
    names = []
    for part in raw.split(","):
        part = part.strip()
        if part in {"1", "2", "3", "4", "5"}:
            names.append(priority_queue(part))
        elif part == "spam":
            names.append(SPAM)
        elif part in WORK_QUEUES:
            names.append(part)
        else:
            raise ValueError(f"unknown queue {part}")
    return names


class MemoryBroker:
    def __init__(self):
        self.queues = {name: [] for name in [*WORK_QUEUES, DLQ, FAILED_SNOW]}

    async def publish(self, queue_name, payload, headers=None):
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
        for name in (DLQ, FAILED_SNOW):
            queue = await self.channel.declare_queue(name, durable=True)
            await queue.bind(self.exchange, routing_key=name)
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

    async def consume(self, handler, on_exhausted=None):
        async def _on_message(message):
            await _deliver(self, message, handler, on_exhausted)

        for name in queues_from_env():
            queue = await self.channel.get_queue(name)
            await queue.consume(_on_message)

    async def close(self):
        if self.connection is not None:
            await self.connection.close()


async def connect_broker(url=None, retries=30):
    import asyncio

    last = None
    for _ in range(retries):
        broker = RabbitBroker(url or os.environ["RABBIT_URL"])
        try:
            await broker.connect()
            return broker
        except Exception as exc:
            last = exc
            await broker.close()
            await asyncio.sleep(1)
    raise last


def _failure_queue(payload):
    if payload.get("queue_name") == SPAM:
        return DLQ
    return FAILED_SNOW


async def _deliver(broker, message, handler, on_exhausted=None):
    from logging_setup import log_event

    async with message.process(requeue=False, ignore_processed=True):
        payload = json.loads(message.body.decode())
        try:
            await handler(payload)
        except Exception as exc:
            headers = dict(message.headers or {})
            attempts = int(headers.get("x-attempts", 0)) + 1
            exhausted = attempts >= MAX_ATTEMPTS
            destination = _failure_queue(payload) if exhausted else payload.get("queue_name")
            if destination == FAILED_SNOW:
                event_name = FAILED_SNOW
            elif exhausted:
                event_name = "dlq"
            else:
                event_name = "retry"
            log_event(
                event_name,
                correlation_id=payload.get("correlation_id"),
                message_id=payload.get("id"),
                queue=destination,
                error=type(exc).__name__,
                attempt=attempts,
            )
            if exhausted:
                await broker.publish(destination, payload, headers={"x-attempts": attempts, "error": type(exc).__name__})
                if on_exhausted is not None:
                    await on_exhausted(payload)
            else:
                await broker.publish(
                    payload["queue_name"],
                    payload,
                    headers={"x-attempts": attempts},
                )
