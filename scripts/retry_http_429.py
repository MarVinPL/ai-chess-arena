#!/usr/bin/env python3
"""Retry a CI build only on explicit HTTP 429 rate-limit diagnostics."""
import re
import subprocess
import sys
import time
from collections import deque

RATE_LIMIT = re.compile(
    r"(?:status(?:\s+code)?\s*[:=]?\s*429\b|\bHTTP(?:/\d(?:\.\d)?)?\s+429\b|\b429\s+Too Many Requests\b)",
    re.IGNORECASE,
)
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def run(command, sleep=time.sleep):
    for attempt in range(3):
        limited = False
        tail = deque(maxlen=80)
        with subprocess.Popen(command, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True,
                              errors="replace", bufsize=1) as process:
            for line in process.stdout:
                print(line, end="", flush=True)
                tail.append(ANSI.sub("", line))
            status = process.wait()
        if status == 0:
            return 0
        # Only retry a rate-limit diagnostic in the terminal failure output.
        limited = any(RATE_LIMIT.search(line) for line in tail)
        if not limited or attempt == 2:
            return status if status > 0 else 128 - status
        delay = (30, 60)[attempt]
        print(f"HTTP 429: retry {attempt + 2}/3 after {delay}s", flush=True)
        sleep(delay)
    raise AssertionError("unreachable")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("Usage: retry_http_429.py COMMAND [ARGS...]")
    raise SystemExit(run(sys.argv[1:]))
