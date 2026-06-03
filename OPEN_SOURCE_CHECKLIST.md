# Open Source Checklist

## Current Status

- Repository is in open-source preparation.
- Repository is not publicly released.
- No GitHub Release is being created in this round.
- No final project `LICENSE` file is being added in this round.
- PyQt5 remains in use, so final license strategy is still pending.

## Completed In This Cleanup

- Removed old user-system placeholder text from UI code and project documents.
- Converted `README.md` into a Windows local-tool overview with current license status.
- Converted `HANDOFF.md` into a public-facing handoff document.
- Removed machine-specific absolute paths from public-facing documents.
- Added third-party dependency notes.
- Added contribution guidance.
- Added security and privacy guidance.
- Kept detection algorithms, cache format, export naming and image quality behavior unchanged.

## Must Finish Before Public Release

- Choose the final project license, or migrate Qt binding if needed.
- Add the final `LICENSE` file.
- Confirm third-party notices for the exact packaged dependency versions.
- Re-run a repository scan for absolute paths and private project data.
- Add public screenshots or a short demo video that uses non-private material.
- Write release notes for the first public version.
- Decide the public issue and pull request process.
- Build and verify the public Windows package.

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
