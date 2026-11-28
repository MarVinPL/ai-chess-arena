# Worker application icons

Desktop icons preserve the original 64 × 64 two-color artwork from main
(2e098d3), using nearest-neighbor resampling without changing its RGBA palette.
Android retains the separately approved Arena pawn design.

**Glyph provenance:** the vector pawn outline uses the open DejaVu Sans U+265F glyph (DejaVu/Bitstream Vera open font licence, © 2003 Bitstream, Inc.; DejaVu changes in the public domain; <https://dejavu-fonts.github.io/>). No font file is included. The heading symbol and the Android icon therefore share the same Unicode chess-piece identity.

| File (src-tauri/icons/) | Dimensions | Purpose |
| --- | --- | --- |
| icon.png | 512 × 512 | Shared desktop master |
| 32x32.png | 32 × 32 | Linux |
| 128x128.png | 128 × 128 | Linux |
| 128x128@2x.png | 256 × 256 | Linux HiDPI |
| icon.ico | 32, 16, 24, 48, 64, 256 px | Windows, PNG-backed RGBA layers |
| android-pawn.svg | Vector | Android launcher |

Tauri bundle.icon registers the five desktop resources. Bundle.active remains
false. Windows and Linux builds remain paused (Windows reactivation: #381).

Android CI generates the pawn resources after Android project initialization,
using an isolated output directory (android-generated), then copies and
verifies the generated mipmaps before compiling. This does not overwrite the
desktop assets. Signing, token storage and update handling are unchanged.

Selective CI remains as established by #214: desktop-only icon variants do
not trigger Worker builds while desktop jobs are paused; icon.png is shared,
and android-pawn.svg selects Android. Workflow-only and documentation-only
changes do not trigger app builds. Artifact reuse is separate (#223).

Validation: python -m pytest -q tests/test_worker_icon_assets.py
tests/test_worker_platform_icons.py (on one command line). Tests verify
all pixel values against the original decoded RGBA digest, ICO directory and
payloads, bundle config, Android generation order and platform routing.
Physical Android validation is deferred to #311; desktop builds remain paused.
