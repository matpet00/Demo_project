#!/usr/bin/env python3
"""Render docs/images/flow.gif — animated architecture walkthrough for README."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "images" / "flow.gif"

W, H = 1120, 630
PAPER = (239, 228, 207)
INK = (27, 23, 18)
MUTED = (109, 98, 84)
STAMP = (157, 28, 20)
SENT = (28, 107, 69)
LANE = (247, 241, 228)
RULE = (42, 36, 28)
ACCENT = (45, 90, 140)


def _fonts():
    candidates = (
        "/System/Library/Fonts/Supplemental/Andale Mono.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/Library/Fonts/Arial.ttf",
    )
    for path in candidates:
        if Path(path).is_file():
            return (
                ImageFont.truetype(path, 22),
                ImageFont.truetype(path, 17),
                ImageFont.truetype(path, 14),
            )
    default = ImageFont.load_default()
    return default, default, default


TITLE, BODY, SMALL = _fonts()


def canvas(step: str, title: str) -> Image.Image:
    img = Image.new("RGB", (W, H), PAPER)
    draw = ImageDraw.Draw(img)
    draw.text((40, 28), "DEMO_PROJECT · ITIL EVENT FLOW", fill=STAMP, font=SMALL)
    draw.text((40, 52), title, fill=INK, font=TITLE)
    draw.line((40, 92, W - 40, 92), fill=RULE, width=2)
    draw.text((W - 220, 34), step, fill=MUTED, font=SMALL)
    return img


def box(draw, xy, label, sub="", fill=LANE, accent=RULE):
    x0, y0, x1, y1 = xy
    draw.rounded_rectangle(xy, radius=8, fill=fill, outline=accent, width=2)
    draw.text((x0 + 14, y0 + 12), label, fill=INK, font=BODY)
    if sub:
        draw.text((x0 + 14, y0 + 38), sub, fill=MUTED, font=SMALL)


def draw_arrow(draw, start, end, color=RULE):
    draw.line([start, end], fill=color, width=3)
    ex, ey = end
    sx, sy = start
    if abs(ex - sx) >= abs(ey - sy):
        tip = (-12, -6) if ex > sx else (12, -6)
    else:
        tip = (-6, -12) if ey > sy else (-6, 12)
    draw.polygon(
        [(ex, ey), (ex + tip[0], ey + tip[1]), (ex + tip[0], ey - tip[1])],
        fill=color,
    )


def code_block(draw, xy, lines, fill=(32, 30, 28)):
    x0, y0, x1, y1 = xy
    draw.rounded_rectangle(xy, radius=8, fill=fill, outline=RULE, width=1)
    y = y0 + 12
    for line in lines:
        draw.text((x0 + 14, y), line, fill=(220, 215, 200), font=SMALL)
        y += 20


def frame_01():
    img = canvas("1 / 9", "Caller hits the API gateway")
    draw = ImageDraw.Draw(img)
    box(draw, (60, 130, 280, 220), "Monitoring / app", "OAuth client")
    box(draw, (420, 110, 700, 250), "WSO2 API Manager", "POST /desk/v1/events\nBearer token + JSON body", accent=STAMP)
    box(draw, (820, 150, 1060, 230), "JSON payload", '{"description":"…"}')
    draw_arrow(draw, (280, 175), (420, 175))
    draw_arrow(draw, (700, 175), (820, 175))
    draw.text((60, 280), "No ServiceNow credentials on the client.", fill=MUTED, font=BODY)
    draw.text((60, 310), "Gateway owns auth, throttling, and the public contract.", fill=MUTED, font=BODY)
    return img


def frame_02():
    img = canvas("2 / 9", "WSO2 mediation enriches the call")
    draw = ImageDraw.Draw(img)
    box(draw, (80, 120, 360, 240), "WSO2 mediation", "Validate OAuth token\nApply Gold subscription policy", accent=STAMP)
    code_block(
        draw,
        (420, 120, 1060, 280),
        [
            "POST /desk/v1/events",
            "Authorization: Bearer …",
            "Content-Type: application/json",
            "",
            "→ adds headers (client cannot forge):",
            "  X-User-Name: alice",
            "  X-User-Uuid: <uuid v5(username)>",
        ],
    )
    draw.text((80, 320), "Identity is computed at the edge, not taken from the JSON body.", fill=MUTED, font=BODY)
    return img


def frame_03():
    img = canvas("3 / 9", "Ingest service accepts and persists")
    draw = ImageDraw.Draw(img)
    box(draw, (60, 130, 320, 230), "Ingest · Quart", "async api.py", accent=ACCENT)
    box(draw, (380, 120, 620, 250), "Validate", "401 if no UUID\n400 if no event text")
    box(draw, (660, 130, 880, 230), "MySQL", "message log\ncorrelation id")
    box(draw, (920, 140, 1060, 220), "202", "queued")
    draw_arrow(draw, (320, 180), (380, 180))
    draw_arrow(draw, (620, 180), (660, 180))
    draw_arrow(draw, (880, 180), (920, 180))
    draw.text((60, 290), "Fast accept path — caller does not wait on ServiceNow.", fill=MUTED, font=BODY)
    return img


def frame_04():
    img = canvas("4 / 9", "Block list or agent triage")
    draw = ImageDraw.Draw(img)
    box(draw, (80, 140, 340, 240), "UUID on block list?", "", fill=LANE)
    box(draw, (420, 110, 640, 200), "Yes → events.spam", "Agent skipped", fill=(243, 215, 209), accent=STAMP)
    box(draw, (420, 220, 700, 320), "No → ReAct agent", "OpenRouter or ScriptedBrain", accent=ACCENT)
    draw_arrow(draw, (340, 190), (420, 155))
    draw_arrow(draw, (340, 190), (420, 270))
    draw.text((80, 360), "ITIL suppression before any ticket system sees the signal.", fill=MUTED, font=BODY)
    return img


def frame_05():
    img = canvas("5 / 9", "ReAct loop — Thought · Action · Observation")
    draw = ImageDraw.Draw(img)
    steps = [
        ("Thought", "Payload has text, no priority yet"),
        ("Action", "lookup_service(description)"),
        ("Observation", "service: checkout-api …"),
        ("Action", "classify_priority(description)"),
        ("Observation", "suggested_priority: 1"),
        ("Answer", '{"priority": 1} → priority1_queue'),
    ]
    y = 118
    for i, (kind, text) in enumerate(steps):
        fill = (230, 240, 250) if kind == "Action" else LANE
        box(draw, (80, y, 980, y + 52), kind, text, fill=fill)
        if i < len(steps) - 1:
            draw_arrow(draw, (530, y + 52), (530, y + 62))
        y += 62
    draw.text((80, 520), "Trace stored on the message for the event board.", fill=MUTED, font=SMALL)
    return img


def frame_06():
    img = canvas("6 / 9", "Publish to RabbitMQ")
    draw = ImageDraw.Draw(img)
    box(draw, (60, 150, 300, 250), "Ingest publisher", "aio-pika", accent=ACCENT)
    box(draw, (360, 120, 760, 280), "Exchange: events (direct)", "Durable message\nrouting key = queue name", accent=RULE)
    for i, name in enumerate(["priority1_queue", "priority2_queue", "…", "priority5_queue", "events.spam"]):
        box(draw, (820, 110 + i * 58, 1060, 158 + i * 58), name, "", fill=LANE)
    draw_arrow(draw, (300, 200), (360, 200))
    draw_arrow(draw, (760, 200), (820, 200))
    draw.text((60, 320), "Decouples “accepted” from “delivered” — absorbs spikes and retries.", fill=MUTED, font=BODY)
    return img


def frame_07():
    img = canvas("7 / 9", "Delivery worker consumes")
    draw = ImageDraw.Draw(img)
    box(draw, (60, 140, 320, 240), "Delivery · Quart", "delivery.py consumer", accent=ACCENT)
    box(draw, (380, 130, 640, 250), "Map message", "source, node, severity\ndescription, message_key\nadditional_info")
    box(draw, (700, 150, 1060, 230), "ServiceNow event API", "not Table API from clients", accent=SENT)
    draw_arrow(draw, (320, 190), (380, 190))
    draw_arrow(draw, (640, 190), (700, 190))
    draw.text((60, 290), "Tries EM paths, then scripted POST /api/now/desk_events/event.", fill=MUTED, font=BODY)
    draw.text((60, 320), "Status sent + event number stored on the message.", fill=MUTED, font=BODY)
    return img


def frame_08():
    img = canvas("8 / 9", "Failure and spam paths stay visible")
    draw = ImageDraw.Draw(img)
    box(draw, (70, 130, 330, 220), "Blocked sender", "events.spam", fill=(243, 215, 209), accent=STAMP)
    box(draw, (70, 250, 330, 340), "Mailpit / SMTP", "Spammer detected\nnot_processed")
    box(draw, (400, 130, 720, 220), "ServiceNow error", "3 attempts", accent=STAMP)
    box(draw, (400, 250, 720, 340), "failed_snow_delivery", "still an event — no auto-incident")
    box(draw, (780, 180, 1060, 300), "events.dlq", "mail exhausted", fill=LANE)
    draw_arrow(draw, (330, 175), (400, 175))
    draw_arrow(draw, (330, 295), (400, 295))
    draw.text((70, 400), "Nothing silently drops; poison messages remain inspectable in RabbitMQ.", fill=MUTED, font=BODY)
    return img


def frame_09():
    img = canvas("9 / 9", "Observable end-to-end")
    draw = ImageDraw.Draw(img)
    box(draw, (60, 130, 280, 230), "Ingest logs", "JSON stdout", accent=ACCENT)
    box(draw, (320, 130, 540, 230), "Delivery logs", "correlation_id", accent=ACCENT)
    box(draw, (580, 120, 820, 250), "Grafana + Loki", "filter user / queue")
    box(draw, (860, 140, 1060, 230), "Event board", ":8080 UI")
    draw_arrow(draw, (280, 180), (320, 180))
    draw_arrow(draw, (540, 180), (580, 180))
    draw_arrow(draw, (820, 180), (860, 180))
    draw.text(
        (60, 300),
        "Recruiter summary: gateway auth → async triage → queue buffer → SN event delivery.",
        fill=INK,
        font=BODY,
    )
    draw.text((60, 340), "Events ≠ incidents · preparation.py is setup, not runtime.", fill=MUTED, font=BODY)
    return img


FRAMES = (
    frame_01,
    frame_02,
    frame_03,
    frame_04,
    frame_05,
    frame_06,
    frame_07,
    frame_08,
    frame_09,
)


def main():
    images = [fn().convert("P", palette=Image.Palette.ADAPTIVE) for fn in FRAMES]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    images[0].save(
        OUT,
        save_all=True,
        append_images=images[1:],
        duration=3200,
        loop=0,
        optimize=True,
    )
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KiB)")


if __name__ == "__main__":
    main()
