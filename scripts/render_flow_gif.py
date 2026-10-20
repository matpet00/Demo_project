#!/usr/bin/env python3
"""Capture docs/flow.html?capture=1 to docs/images/flow.gif."""

from __future__ import annotations

import asyncio
from io import BytesIO
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "docs" / "flow.html"
OUT = ROOT / "docs" / "images" / "flow.gif"

FRAME_MS = 68


async def capture() -> tuple[list[Image.Image], list[int]]:
    from playwright.async_api import async_playwright

    url = HTML.as_uri() + "?capture=1"
    frames: list[Image.Image] = []
    durations: list[int] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page(viewport={"width": 1280, "height": 720}, device_scale_factor=1)
        await page.goto(url, wait_until="networkidle")
        total = await page.evaluate("window.totalFlowFrames")
        prev_key: bytes | None = None
        for i in range(total):
            await page.evaluate(f"window.renderFlowFrame({i})")
            png = await page.screenshot(type="png")
            key = png
            if prev_key == key and frames:
                durations[-1] += FRAME_MS
                continue
            prev_key = key
            frames.append(Image.open(BytesIO(png)).convert("RGB"))
            durations.append(FRAME_MS)
        await browser.close()
    return frames, durations


def save_gif(frames: list[Image.Image], durations: list[int]) -> None:
    w, h = 880, 495
    resized = [im.resize((w, h), Image.Resampling.LANCZOS) for im in frames]
    palette = resized[0].quantize(colors=64, method=Image.Quantize.MEDIANCUT)
    indexed = [im.quantize(palette=palette, dither=Image.Dither.FLOYDSTEINBERG) for im in resized]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    indexed[0].save(
        OUT,
        save_all=True,
        append_images=indexed[1:],
        duration=durations,
        loop=0,
        optimize=True,
    )


def main() -> None:
    frames, durations = asyncio.run(capture())
    save_gif(frames, durations)
    print(
        f"wrote {OUT} ({OUT.stat().st_size // 1024} KiB, "
        f"{len(frames)} unique frames, {sum(durations) / 1000:.1f}s)"
    )


if __name__ == "__main__":
    main()
