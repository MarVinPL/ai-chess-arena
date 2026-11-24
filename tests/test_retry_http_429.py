"""Exercise rate-limit retries without network access or actual delays."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

spec = importlib.util.spec_from_file_location(
    "retry_http_429", Path(__file__).resolve().parents[1] / "scripts/retry_http_429.py"
)
retry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(retry)


class RetryHttp429Tests(unittest.TestCase):
    def scenario(self, messages, statuses):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            counter = root / "counter"
            code = (
                "from pathlib import Path; import sys; "
                f"p=Path({str(counter)!r}); "
                "i=int(p.read_text()) if p.exists() else 0; "
                "p.write_text(str(i+1)); "
                f"print({messages!r}[i]); sys.exit({statuses!r}[i])"
            )
            delays = []
            status = retry.run([sys.executable, "-c", code], sleep=delays.append)
            return status, int(counter.read_text()), delays

    def test_success(self):
        self.assertEqual(self.scenario(["ok"], [0]), (0, 1, []))

    def test_recovery(self):
        self.assertEqual(self.scenario(
            ["Received status code 429 from server: Too Many Requests", "ok"], [1, 0]
        ), (0, 2, [30]))

    def test_bounded_retries(self):
        self.assertEqual(self.scenario(["HTTP 429"] * 3, [7] * 3),
                         (7, 3, [30, 60]))

    def test_other_error(self):
        self.assertEqual(self.scenario(["compiler error at line 429"], [2]),
                         (2, 1, []))

    def test_stop_on_non_rate_limit_failure(self):
        self.assertEqual(self.scenario(["HTTP/1.1 429", "HTTP 403"], [1, 4]),
                         (4, 2, [30]))


if __name__ == "__main__":
    unittest.main()
