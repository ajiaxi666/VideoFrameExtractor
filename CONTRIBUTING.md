# Contributing

VideoFrameExtractor community edition is licensed under GPLv3.

The project is currently maintained as an author-led public codebase. External code contributions are not accepted automatically. Please discuss substantial changes before opening a pull request.

If a future contribution needs to be used outside the GPLv3 community edition, the contributor must provide an additional written license grant or contributor agreement before that code can be merged.

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

## Contribution Boundary

By contributing to the public repository, you agree that your contribution may be distributed under GPLv3. Do not submit code copied from incompatible projects, private client work, or source that you cannot license.

Pull requests that introduce unclear ownership, private media, generated cache data, or license ambiguity may be closed without merge.
