# Open Source Checklist

## Current Status

- Repository is prepared for public GPLv3 release.
- Community edition uses GPLv3.
- No GitHub Release is being created in this round.
- PyQt5 remains in use, so GPLv3 is the public license path for this codebase.

## Completed In This Cleanup

- Removed old user-system placeholder text from UI code and project documents.
- Converted `README.md` into a Windows local-tool overview with current license status.
- Converted `HANDOFF.md` into a public-facing handoff document.
- Removed machine-specific absolute paths from public-facing documents.
- Added third-party dependency notes.
- Added contribution guidance.
- Added security and privacy guidance.
- Added GPLv3 `LICENSE`.
- Added contribution boundary for external code.
- Kept detection algorithms, cache format, export naming and image quality behavior unchanged.

## Recommended Before Public Release Package

- Confirm third-party notices for the exact packaged dependency versions.
- Re-run a repository scan for absolute paths and private project data.
- Add public screenshots or a short demo video that uses non-private material.
- Write release notes for the first public version.
- Build and verify the public Windows package.

## Optional Later Work

- Migrate to PySide6 if a future non-GPL public licensing strategy is needed.
- Add a formal contributor agreement if external code contributions should be reused outside the GPLv3 community edition.

## Files That Should Stay Out Of Git

- `.venv/`
- `build/`
- `dist/`
- `release/`
- original videos
- exported image folders
- detection result JSON files for private projects
- feature cache files
- local IDE settings
