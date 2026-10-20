import json
import os
import re

import httpx

from decorators import TOOLS, tool

DESCRIPTION_SCHEMA = {
    "type": "object",
    "properties": {"description": {"type": "string"}},
    "required": ["description"],
}
MAX_STEPS = 6
SYSTEM = (
    "You triage one IT event. Call lookup_service, then classify_priority. "
    'After both observations, answer with a JSON object {"priority": N} '
    "where N is an integer from 1 to 5. 1 is the most severe. "
    "Do not answer with a priority before both tools have run."
)


def _has(text, word):
    if len(word) <= 3:
        return re.search(rf"\b{re.escape(word)}\b", text) is not None
    return word in text


@tool("Find the service named in an event description.", DESCRIPTION_SCHEMA)
def lookup_service(description):
    text = description.lower()
    catalog = (
        ("checkout", "checkout-api", "Payments traffic. An outage here is severe."),
        ("payment", "checkout-api", "Payments traffic. An outage here is severe."),
        ("database", "orders-db", "The order database. Down means priority 1."),
        ("proxy", "edge-proxy", "The edge proxy. Latency here is a major degradation."),
        ("latency", "edge-proxy", "Latency at the edge is a major degradation."),
    )
    for needle, service, note in catalog:
        if needle in text:
            return json.dumps({"service": service, "note": note})
    return json.dumps({"service": "unknown", "note": "No catalog match. Judge the wording."})


@tool("Suggest priority 1-5 from the event wording. 1 is the most severe.", DESCRIPTION_SCHEMA)
def classify_priority(description):
    text = description.lower()
    rules = (
        (1, ("outage", "down", "unavailable", "critical", "vypadek", "výpadek")),
        (2, ("slow", "latency", "degraded", "zpomal")),
        (3, ("error", "fail", "exception", "timeout")),
        (4, ("warn", "warning", "disk")),
        (5, ("info", "inform", "notice", "recovered", "ok")),
    )
    for priority, words in rules:
        if any(_has(text, word) for word in words):
            return json.dumps({"suggested_priority": priority, "reason": "keyword match"})
    return json.dumps({"suggested_priority": 3, "reason": "no strong signal"})


def tool_specs():
    return [
        {"name": spec["name"], "description": spec["description"], "schema": spec["schema"]}
        for spec in TOOLS.values()
        if spec["name"] in {"lookup_service", "classify_priority"}
    ]


def invoke(name, payload):
    spec = TOOLS.get(name)
    if spec is None:
        return json.dumps({"error": f"unknown tool {name}"})
    description = payload.get("description", "") if isinstance(payload, dict) else ""
    result = spec["fn"](description)
    return result if isinstance(result, str) else json.dumps(result)


class ScriptedBrain:
    def __init__(self):
        self.description = ""
        self.phase = 0
        self.last_observation = ""

    def start(self, description):
        self.description = description
        self.phase = 0
        self.last_observation = ""

    def observe(self, decision, observation):
        self.last_observation = observation
        self.phase += 1

    async def step(self):
        if self.phase == 0:
            return {
                "thought": "The payload has a description and no priority. I look up the service it names.",
                "action": "lookup_service",
                "input": {"description": self.description},
            }
        if self.phase == 1:
            return {
                "thought": "I have the service note. Next I classify the wording onto priority 1 to 5.",
                "action": "classify_priority",
                "input": {"description": self.description},
            }
        suggested = json.loads(self.last_observation)["suggested_priority"]
        return {
            "thought": f"Classification returned {suggested}. I assign priority {suggested}.",
            "final": int(suggested),
        }


class OpenAICompatibleBrain:
    def __init__(self, api_key, model, base_url, client=None):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.AsyncClient(timeout=45)
        self.chat = []

    def start(self, description):
        self.chat = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": description},
        ]

    def observe(self, decision, observation):
        self.chat.append(
            {
                "role": "assistant",
                "content": decision.get("thought") or "",
                "tool_calls": [
                    {
                        "id": decision["tool_call_id"],
                        "type": "function",
                        "function": {
                            "name": decision["action"],
                            "arguments": json.dumps(decision.get("input") or {}),
                        },
                    }
                ],
            }
        )
        self.chat.append(
            {
                "role": "tool",
                "tool_call_id": decision["tool_call_id"],
                "content": observation,
            }
        )

    async def step(self):
        response = await self.client.post(
            f"{self.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "http://localhost:5000",
                "X-Title": "event-desk",
            },
            json={
                "model": self.model,
                "messages": self.chat,
                "tools": [_openai_tool(spec) for spec in tool_specs()],
            },
        )
        response.raise_for_status()
        message = response.json()["choices"][0]["message"]
        tool_calls = message.get("tool_calls") or []
        if tool_calls:
            call = tool_calls[0]
            arguments = call["function"].get("arguments") or "{}"
            if isinstance(arguments, str):
                arguments = json.loads(arguments or "{}")
            return {
                "thought": message.get("content") or f"I call {call['function']['name']}.",
                "action": call["function"]["name"],
                "input": arguments,
                "tool_call_id": call.get("id") or "call-1",
            }
        return {
            "thought": message.get("content") or "The model returned a priority.",
            "final": parse_priority(message.get("content") or ""),
        }


def make_brain():
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        return ScriptedBrain()
    return OpenAICompatibleBrain(
        api_key=api_key,
        model=os.environ.get("OPENROUTER_MODEL", "poolside/laguna-xs-2.1:free"),
        base_url=os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
    )


async def run_react(description, brain):
    try:
        return await _loop(description, brain)
    except Exception as exc:
        if isinstance(brain, ScriptedBrain):
            raise
        result = await _loop(description, ScriptedBrain())
        result["trace"].insert(
            0,
            {
                "thought": f"Model call failed ({type(exc).__name__}). Used the scripted fallback.",
                "action": None,
                "action_input": None,
                "observation": None,
            },
        )
        return result


async def _loop(description, brain):
    brain.start(description)
    trace = []
    for _ in range(MAX_STEPS):
        decision = await brain.step()
        if "final" in decision:
            priority = int(decision["final"])
            if priority not in range(1, 6):
                priority = 3
            trace.append(
                {
                    "thought": decision.get("thought"),
                    "action": None,
                    "action_input": None,
                    "observation": None,
                }
            )
            return {"priority": priority, "trace": trace}
        observation = invoke(decision["action"], decision.get("input") or {})
        brain.observe(decision, observation)
        trace.append(
            {
                "thought": decision.get("thought"),
                "action": decision.get("action"),
                "action_input": decision.get("input"),
                "observation": observation,
            }
        )
    return {"priority": 3, "trace": trace}


def parse_priority(text):
    match = re.search(r"\{[^{}]*\"priority\"\s*:\s*([1-5])[^{}]*\}", text)
    if match:
        return int(match.group(1))
    match = re.search(r"priority\s*[:=]\s*([1-5])", text, re.I)
    if match:
        return int(match.group(1))
    stripped = text.strip()
    if stripped in {"1", "2", "3", "4", "5"}:
        return int(stripped)
    raise ValueError("model did not return a priority")


def _openai_tool(spec):
    return {
        "type": "function",
        "function": {
            "name": spec["name"],
            "description": spec["description"],
            "parameters": spec["schema"],
        },
    }
