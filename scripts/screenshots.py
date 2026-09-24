"""Take the README screenshots from a running demo server (make_demo_db.py).

Drives a headless Edge/Chrome over the DevTools protocol, so it needs no
browser-automation package — only `websockets`, which uvicorn already pulls in.

    python scripts/make_demo_db.py
    DATABASE_PATH=demo.db python -m uvicorn app.main:app --port 8766
    python scripts/screenshots.py [http://localhost:8766]
"""
import asyncio
import base64
import io
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import websockets
from PIL import Image

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8766"
OUT = Path(__file__).resolve().parent.parent / "docs" / "images"
PORT = 9333
SIZE = (1280, 760)

BROWSERS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    shutil.which("chromium") or "", shutil.which("google-chrome") or "",
]


class Page:
    def __init__(self, ws):
        self.ws, self.n = ws, 0

    async def send(self, method, **params):
        self.n += 1
        await self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            msg = json.loads(await self.ws.recv())
            if msg.get("id") == self.n:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    async def js(self, expr):
        r = await self.send("Runtime.evaluate", expression=expr, awaitPromise=True, returnByValue=True)
        return r.get("result", {}).get("value")

    async def settle(self, seconds):
        # wait out the page's own fades and the thumbnails' decoding
        await asyncio.sleep(seconds)
        await self.js("Promise.all([...document.images].filter(i => !i.complete)"
                      ".map(i => new Promise(r => { i.onload = i.onerror = r; })))")

    async def shot(self, name):
        # captured at 2x, stored at 1600px: sharp on a retina screen at the
        # width GitHub shows, and photos as JPEG so the repo stays light
        data = (await self.send("Page.captureScreenshot", format="png"))["data"]
        im = Image.open(io.BytesIO(base64.b64decode(data)))
        im = im.resize((1600, round(im.height * 1600 / im.width)), Image.LANCZOS)
        if name.endswith(".jpg"):
            im.convert("RGB").save(OUT / name, quality=85, optimize=True, progressive=True)
        else:
            im.save(OUT / name, optimize=True)
        print("wrote", OUT / name)


async def run(ws_url):
    async with websockets.connect(ws_url, max_size=None) as ws:
        page = Page(ws)
        await page.send("Page.enable")
        await page.send("Emulation.setDeviceMetricsOverride", width=SIZE[0], height=SIZE[1],
                        deviceScaleFactor=2, mobile=False)
        await page.send("Page.navigate", url=BASE)
        await page.settle(2)
        # the look the README shows: Sepia, the whole library, cinema on
        await page.js("localStorage.setItem('theme','sepia'); localStorage.setItem('libMode','');"
                      "localStorage.setItem('cinema','1'); localStorage.setItem('themeSort','count')")
        await page.send("Page.reload")
        await page.settle(3)
        await page.shot("browse.jpg")

        # cinema: Spring, a 16:9 film (Sintel is scope and letterboxes itself)
        await page.js("[...document.querySelectorAll('#browseGrid .card')]"
                      ".find(c => c._video.id === 'WhWc3b3KhnY').querySelector('.thumbwrap').click()")
        await page.settle(7)
        await page.js("document.getElementById('cinemaTitle').classList.add('show')")
        await page.settle(1)
        await page.shot("cinema.jpg")
        await page.js("closePlayer()")

        await page.js("document.getElementById('tabHistory').click()")
        await page.settle(2)
        await page.shot("history.png")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    exe = next(b for b in BROWSERS if b and Path(b).exists())
    profile = tempfile.mkdtemp(prefix="peneira-shots-")
    proc = subprocess.Popen([exe, "--headless=new", f"--remote-debugging-port={PORT}",
                             f"--user-data-dir={profile}", "--hide-scrollbars", "--mute-audio",
                             "--autoplay-policy=no-user-gesture-required", "about:blank"])
    try:
        for _ in range(50):
            try:
                pages = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/list"))
                ws_url = next(p["webSocketDebuggerUrl"] for p in pages if p["type"] == "page")
                break
            except Exception:
                time.sleep(0.2)
        else:
            raise SystemExit("browser did not start")
        asyncio.run(run(ws_url))
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        shutil.rmtree(profile, ignore_errors=True)


if __name__ == "__main__":
    main()
