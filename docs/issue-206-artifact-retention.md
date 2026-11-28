# Issue #206: Worker artifact retention

The retention job is restricted to main pushes and serialized across runs.
Each platform is eligible only when its corresponding build job succeeds.
Selection requires a unique, unexpired replacement and strictly older run
identifiers and timezone-aware creation timestamps. Ambiguous metadata
causes conservative retention. Candidates are revalidated against fresh
metadata. Desktop build pause remains unchanged.

## Verification

- Base main: 603d6ba4bb50645898d88ecd7facd25329492221.
- Targeted regression: 13 passed in 0.14 s.
- Full Python regression: 1467 passed, 1 skipped, 138 subtests passed
  in 81.45 s; pytest exit 0.
- Tests use synthetic metadata; no live artifact cleanup was performed.
- Workflow integration reviewed statically; live main cleanup not verified.
- VPS evidence: /tmp/issue206-full-regression-pty.log and
  /tmp/issue206-full-regression-pty.exit.

No production deployment is part of this change.
