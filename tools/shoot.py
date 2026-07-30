#!/usr/bin/env python3
"""
Screenshot the dashboard so a UI change can be looked at instead of imagined.

    python3 tools/shoot.py                       # all shots, default host
    python3 tools/shoot.py --url http://… --out /tmp/ui

Pairs with tools/seed_demo.py: seed a SQLite database, run the server against
it, then shoot. Every shot is full-page at a fixed width so two runs diff
cleanly.
"""
import argparse
import asyncio
import os
import sys


async def run(url: str, out: str, width: int):
    from playwright.async_api import async_playwright
    os.makedirs(out, exist_ok=True)
    async with async_playwright() as p:
        # The pinned browser in this image may not match the installed
        # playwright build, and re-downloading is blocked. Point at the one
        # that is here rather than failing on a version number.
        exe = None
        for cand in ("/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
                     "/opt/pw-browsers/chromium/chrome-linux/chrome"):
            if os.path.exists(cand):
                exe = cand
                break
        b = await p.chromium.launch(args=["--no-sandbox"], executable_path=exe)
        pg = await b.new_page(viewport={"width": width, "height": 1100},
                              device_scale_factor=2)
        errors = []
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: errors.append(str(e)))
        await pg.goto(url, wait_until="networkidle")
        await pg.wait_for_timeout(1500)
        await pg.screenshot(path=f"{out}/01-list.png", full_page=True)

        # Open each seeded review: the rich card, the candidate card, the thin one.
        for i, name in enumerate(["rich", "candidates", "untraceable"]):
            cards = await pg.query_selector_all("[data-review-id], .review-item, .rev-card")
            if i < len(cards):
                await cards[i].click()
                await pg.wait_for_timeout(1200)
                await pg.screenshot(path=f"{out}/0{i+2}-{name}.png", full_page=True)
        if errors:
            print("console errors:")
            for e in errors[:10]:
                print("  ", e[:200])
        else:
            print("no console errors")
        await b.close()
    print(f"shots in {out}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:5055/")
    ap.add_argument("--out", default="/tmp/ui")
    ap.add_argument("--width", type=int, default=1440)
    a = ap.parse_args()
    asyncio.run(run(a.url, a.out, a.width))
    return 0


if __name__ == "__main__":
    sys.exit(main())
