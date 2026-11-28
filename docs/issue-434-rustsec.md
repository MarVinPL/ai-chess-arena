# RustSec remediation — #434

Baseline: main 82117089b33f125bac73c006e2cb48568601b676; advisory-db 3eebb903ba6c43f60c57dc0e15fa61eef06231d1 (previously reviewed).

| Advisory | Before | After / disposition |
| --- | --- | --- |
| RUSTSEC-2026-0594 | cookie 0.18.2 | 0.18.3, patched threshold; Android graph includes cookie through Tauri/runtime/Wry, default feature only. Advisory requires private-cookie operations after fork. |
| RUSTSEC-2026-0543 | chrono 0.4.45 | Unmaintained; no patched version in reviewed advisory. Not shown in locked Android ARM64 graph. Track upstream migration. |
| RUSTSEC-2024-0370 | proc-macro-error 1.0.4 | Unmaintained; no patched version. Not shown in locked Android ARM64 graph. Track upstream macro migration. |
| RUSTSEC-2024-0429 | glib 0.18.5 | Unsoundness, patched >=0.20.0. Not shown in locked Android ARM64 graph. Desktop remains paused; upgrade compatible GTK/Tauri stack before desktop activation. |

Evidence: `cargo update -p cookie --precise 0.18.3 --offline`; unrelated cssparser-macros/syn resolver change reverted; `cargo tree --locked --target aarch64-linux-android -e features -i cookie` succeeded after fetching cookie; same command offline succeeded. `cargo tree --locked --offline --target aarch64-linux-android -i PACKAGE` for each remaining package returned nothing to print. Lockfile diff restricted to cookie version/checksum. This is target-graph triage, not proof of absence from every host/build configuration. No advisory suppressed. Full cargo-audit and compilation not repeated; Android CI and regression remain required. No deployment.
