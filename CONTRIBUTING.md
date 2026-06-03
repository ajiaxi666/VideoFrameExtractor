# Contributing

VideoFrameExtractor is currently in open-source preparation. The repository is not publicly released yet, and no final project license has been selected.

## Setup

Use Windows PowerShell or Command Prompt from the project root.

```cmd
python -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe main.py
```

You can also use the helper scripts:

```cmd
setup_windows.cmd
run_app.cmd
```

## Build

```powershell
powershell -ExecutionPolicy Bypass -File .\build_portable.ps1
```

Build outputs should stay out of source control.

## Checks

Before submitting changes, run:

```cmd
.venv\Scripts\python.exe -m py_compile main.py ui\main_window.py core\shot_detector.py core\feature_cache.py core\image_saver.py core\video_processor.py core\frame_selector.py
```

For UI changes, also start the app and smoke-test:

- choose a video
- drag a video into the window
- detect shots
- load cached results
- move the frame slider
- jump to start, middle and end frames
- set the current frame as a keyframe
- export keyframes
- export start, middle and end frames

## Source Control Hygiene

Please do not commit:

- original videos
- exported images
- detection caches
- local project result files containing private video context
- `.venv/`
- `build/`
- `dist/`
- `release/`
- machine-specific absolute paths
- credentials or private configuration

Keep changes focused. Avoid unrelated refactors when fixing a narrow bug.
