import json

import httpx

from agents import ScriptedBrain, parse_priority, run_react


class Boom:
    def start(self, description):
        return None

    def observe(self, decision, observation):
        return None

    async def step(self):
        raise RuntimeError("offline")


def _tool_response(name, arguments):
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": f"call-{name}",
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(arguments)},
                        }
                    ],
                }
            }
        ]
    }


async def test_scripted_priorities():
    outage = await run_react("checkout-api outage, payments are down", ScriptedBrain())
    slow = await run_react("edge-proxy latency is high and checkout is slow", ScriptedBrain())
    info = await run_react("nightly report finished, informational notice", ScriptedBrain())
    assert outage["priority"] == 1
    assert slow["priority"] == 2
    assert info["priority"] == 5
    actions = [step["action"] for step in outage["trace"]]
    assert actions[:2] == ["lookup_service", "classify_priority"]
    assert "assign priority 1" in outage["trace"][-1]["thought"]


async def test_model_failure_uses_scripted_trace():
    result = await run_react("database is down", Boom())
    assert result["priority"] == 1
    assert "Model call failed" in result["trace"][0]["thought"]
    assert result["trace"][1]["action"] == "lookup_service"


async def test_openai_brain_follows_tool_calls():
    from agents import OpenAICompatibleBrain

    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            body = _tool_response("lookup_service", {"description": "informational notice"})
        elif calls["n"] == 2:
            body = _tool_response("classify_priority", {"description": "informational notice"})
        else:
            body = {"choices": [{"message": {"role": "assistant", "content": '{"priority": 1}'}}]}
        return httpx.Response(200, json=body)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    brain = OpenAICompatibleBrain("test-key", "poolside/laguna-xs-2.1:free", "https://openrouter.ai/api/v1", client)
    result = await run_react("nightly report finished, informational notice", brain)
    assert result["priority"] == 1
    assert [step["action"] for step in result["trace"][:2]] == ["lookup_service", "classify_priority"]
    await client.aclose()


def test_parse_priority_shapes():
    assert parse_priority('{"priority": 4}') == 4
    assert parse_priority("priority: 2") == 2
    assert parse_priority("5") == 5
