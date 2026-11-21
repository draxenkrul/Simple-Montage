# Simple Montage

A small Windows video editor for **cutting** and **changing speed** — nothing more.

![Simple Montage](docs/screenshot.png)

- Split the video at the playhead, delete parts, or cut/keep an In→Out range
- Give every segment its own speed (0.1× – 10×), audio keeps its pitch
- Frame-exact preview with frame stepping; playback can preview the edit (skips cuts, applies speeds)
- Undo / redo
- Export to MP4 (H.264 + AAC) with FFmpeg

## Tech stack

| Part | Used for |
|------|----------|
| Python 3.11+ | language |
| PySide6 (Qt 6) | window, timeline, controls |
| python-mpv + libmpv | video preview (exact seeking, frame stepping, variable speed) |
| FFmpeg / ffprobe | reading video info and exporting |

## Setup (Windows)

1. Install **Python 3.12+** from python.org (tick "Add python.exe to PATH").
2. In the project folder:

   ```bash
   py -m venv .venv
   ```

   ```bash
   .venv\Scripts\activate
   ```

   ```bash
   pip install -e .[dev]
   ```

3. Download the external binaries into the `bin\` folder — see [`bin/README.md`](bin/README.md):
   `ffmpeg.exe`, `ffprobe.exe`, `libmpv-2.dll`.
4. Run:

   ```bash
   python -m simple_montage
   ```

   You can also pass a video path as an argument, or drag a file onto the window.

Run the tests with `pytest`.

## How editing works

The timeline shows the **source** video. Coloured blocks are the parts that are kept
(blue = normal speed, orange = faster, green = slower); hatched areas are cut out.
Click a block to select it, then delete it or change its speed.

| Key | Action |
|-----|--------|
| Space | Play / pause |
| ← / → | Previous / next frame |
| Shift + ← / → | Back / forward 1 second |
| ↑ / ↓ | Previous / next cut point |
| Home / End | Start / end |
| S | Split at playhead |
| Del | Delete selected segment |
| I / O | Set In / Out mark |
| X | Cut out In→Out |
| K | Keep only In→Out |
| Esc | Clear marks |
| Ctrl+Z / Ctrl+Y | Undo / redo |
| Ctrl+O / Ctrl+E | Open / export |
| Mouse wheel on timeline | Zoom (Shift+wheel scrolls) |

## Project layout

```
src/simple_montage/
  app.py              entry point, theme
  main_window.py      main window: actions, playback, editing commands
  player.py           libmpv preview widget
  timeline_widget.py  custom-painted timeline
  model.py            edit model (segments, speeds, undo/redo) — no GUI code
  export.py           builds the FFmpeg command — no GUI code
  export_runner.py    runs FFmpeg in the background with progress
  probe.py            reads video info with ffprobe
  tools.py            finds ffmpeg / ffprobe / libmpv
tests/                unit tests + an end-to-end FFmpeg export test
```

## Ideas for next steps

- Save / load projects
- Reverse and freeze-frame segments
- Smooth slow motion (frame interpolation)
- Multiple source clips and reordering
- Export presets (quality, resolution) and a packaged `.exe` (PyInstaller)
