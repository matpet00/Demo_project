import asyncio
import contextvars
import functools
import os

from logging_setup import log_event

correlation_id = contextvars.ContextVar("correlation_id", default=None)
TOOLS = {}
_IDEMPOTENT = {}


def tool(description, schema):
    def decorate(fn):
        TOOLS[fn.__name__] = {
            "name": fn.__name__,
            "description": description,
            "schema": schema,
            "fn": fn,
        }
        return fn

    return decorate


def retry(times=3, delay=0.0, exceptions=(Exception,)):
    def decorate(fn):
        @functools.wraps(fn)
        async def wrap(*args, **kwargs):
            last = None
            for attempt in range(1, times + 1):
                try:
                    return await fn(*args, **kwargs)
                except exceptions as exc:
                    last = exc
                    if attempt == times:
                        raise
                    await asyncio.sleep(delay * attempt)
            raise last

        return wrap

    return decorate


def traced(fn):
    @functools.wraps(fn)
    async def wrap(*args, **kwargs):
        current = correlation_id.get()
        log_event(fn.__name__, status="start", correlation_id=current)
        try:
            result = await fn(*args, **kwargs)
        except Exception as exc:
            log_event(
                fn.__name__,
                status="error",
                error=type(exc).__name__,
                correlation_id=current,
            )
            raise
        log_event(fn.__name__, status="ok", correlation_id=current)
        return result

    return wrap


def idempotent(key_fn):
    """Cache a return value by key. The event path does not use this."""

    def decorate(fn):
        @functools.wraps(fn)
        async def wrap(*args, **kwargs):
            key = key_fn(*args, **kwargs)
            if key in _IDEMPOTENT:
                return _IDEMPOTENT[key]
            value = await fn(*args, **kwargs)
            _IDEMPOTENT[key] = value
            return value

        return wrap

    return decorate


def clear_idempotent_cache():
    _IDEMPOTENT.clear()


def require_api_key(fn):
    @functools.wraps(fn)
    async def wrap(*args, **kwargs):
        expected = os.environ.get("API_KEY", "")
        if expected:
            from quart import abort, request

            if request.headers.get("X-API-Key") != expected:
                abort(401)
        return await fn(*args, **kwargs)

    return wrap
