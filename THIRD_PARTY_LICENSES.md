# Third-Party Licenses

This document records the main third-party packages used by VideoFrameExtractor. It is a preparation note, not a final legal review.

## Runtime Dependencies

| Package | Version Range | License | Notes |
| --- | --- | --- | --- |
| PyQt5 | `>=5.15.10` | GPL v3 or Riverbank licensing option | Final project licensing depends on this choice. |
| opencv-python | `>=4.8.0` | Apache License 2.0 | Wheel packages may include additional bundled components from OpenCV and FFmpeg-related builds. |
| NumPy | `>=1.26.0` | BSD-3-Clause and compatible component licenses | Local package metadata may show a combined expression such as BSD-3-Clause, 0BSD, MIT, Zlib and CC0-1.0. |
| Pillow | `>=10.0.0` | HPND / MIT-CMU style license | Used for image conversion and icon generation support. |
| PySceneDetect | `>=0.6.3` | BSD-3-Clause | Used for scene detection primitives. |

## Build Tooling

| Package | Role | License | Notes |
| --- | --- | --- | --- |
| PyInstaller | Windows packaging | GPL v2 with exception | Used by `build_portable.ps1` when available in the build environment. |

## Open Items

- Confirm the exact installed package versions before a public release.
- Decide whether the project will use a GPL-compatible license with PyQt5 or migrate to a different Qt binding.
- Re-check bundled binary notices after each packaged build.
