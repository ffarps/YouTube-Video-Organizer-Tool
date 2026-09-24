<p align="center">
  <img src="docs/images/logo.png" width="96" alt="Peneira logo">
</p>

<h1 align="center">Peneira</h1>

<p align="center">
  <b>Your YouTube Watch Later, curated.</b><br>
  <i>O teu "ver mais tarde", escolhido a dedo.</i>
</p>

<p align="center">
  <img src="docs/images/browse.jpg" alt="The library: themes on the left, All / Study / Leisure at the top">
</p>

## What it does

- **Sync** playlists, channels and your Watch Later into one library on your computer
- **Themes** sorted automatically, and your corrections stick
- **Study / Leisure** switch, so work videos and fun ones never mix
- **Cinema mode**: the video fills the window, the controls wait at the sides
- **Drop and skip** what isn't worth your time, in one click
- **Time left** per theme, in hours and in treadmill steps

<p align="center">
  <img src="docs/images/cinema.jpg" width="49%" alt="Cinema mode: the video fills the window, controls in slim rails at the sides">
  <img src="docs/images/history.png" width="49%" alt="History: what you watched and the time left per theme">
</p>

## Quick start (Windows)

1. Install [Python 3.11+](https://www.python.org/downloads/)
2. Double-click `start.bat`. The first run installs everything
3. Paste a playlist link and press **Sync**

For a Desktop icon, run `create-desktop-shortcut.bat` once.
On macOS or Linux: `pip install -e .` then `uvicorn app.main:app` and open
http://localhost:8000.

## Good to know

- Everything stays on your computer: one SQLite file, no account, no cloud.
- Offline copies are one video at a time, on purpose. There's no bulk download.
- Watch Later needs your browser's YouTube cookies. The guide explains how.

**[Read the full guide](docs/GUIDE.md)**: install, settings, offline copies,
troubleshooting and the API.

## License

Apache 2.0. See [LICENSE](LICENSE).
