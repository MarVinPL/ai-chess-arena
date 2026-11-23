"""Require extracted ARM64 native libraries for the Rust dlopen preflight.

Tauri regenerates Android Gradle files each build; patch only the known
generated app module, never a global Gradle setting or external path.
This is idempotent and fails closed if the expected Gradle structure changes.
"""
from pathlib import Path

GRADLE = (
    Path(__file__).resolve().parents[1]
    / "src-tauri/gen/android/app/build.gradle.kts"
)
MARKER = "        jniLibs.useLegacyPackaging = true"
BLOCK = "android {\n    packaging {\n" + MARKER + "\n    }\n"


def main() -> None:
    if not GRADLE.is_file():
        raise SystemExit("Generated Android app Gradle file missing")
    content = GRADLE.read_text(encoding="utf-8")
    if content.count("android {\n") != 1:
        raise SystemExit("Generated Android Gradle layout changed")
    if content.count(MARKER) > 1:
        raise SystemExit("Duplicated native library packaging override")
    if MARKER in content:
        return
    GRADLE.write_text(
        content.replace("android {\n", BLOCK, 1),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
