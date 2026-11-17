#!/usr/bin/env python3
"""Screenshot cockpit tabs with Playwright. Usage: shots.py <url-with-?t=token> <out-dir> [Tab ...] (default: Cockpit Backlog Timeline)"""
import pathlib, sys
from playwright.sync_api import sync_playwright

url, out, tabs = sys.argv[1], pathlib.Path(sys.argv[2]), sys.argv[3:] or ["Cockpit", "Backlog", "Timeline"]
out.mkdir(parents=True, exist_ok=True)
with sync_playwright() as p:
    pg = p.chromium.launch().new_page(viewport=dict(width=1280, height=800))
    pg.goto(url)
    pg.wait_for_selector(".top")
    for t in tabs:
        pg.click(f".nav button:has-text('{t}')")
        pg.wait_for_timeout(500)
        pg.screenshot(path=str(out / f"{t.lower().replace(" ", "-")}.png"))
