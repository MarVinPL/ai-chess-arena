import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "deploy" / "caddy" / "ai-chess-arena.caddy"
README = ROOT / "deploy" / "caddy" / "README.md"


class CaddyModuleTests(unittest.TestCase):
    def test_module_preserves_arena_host_and_loopback_upstream(self):
        text = MODULE.read_text(encoding="utf-8")
        self.assertIn("arena.148-113-182-137.sslip.io", text)
        self.assertIn("reverse_proxy 127.0.0.1:8090", text)
        self.assertIn("basicauth", text)
        self.assertIn("arena {$ARENA_BASIC_AUTH_HASH}", text)

    def test_module_does_not_contain_literal_bcrypt_hash(self):
        text = MODULE.read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"\$2[aby]\$\d{2}\$", text))
        self.assertNotIn("password", text.lower())

    def test_module_contains_only_the_arena_site(self):
        text = MODULE.read_text(encoding="utf-8")
        hosts = re.findall(r"(?m)^([A-Za-z0-9.-]+)\s*\{\s*$", text)
        self.assertEqual(hosts, ["arena.148-113-182-137.sslip.io"])

    def test_migration_documentation_keeps_activation_separate(self):
        text = README.read_text(encoding="utf-8")
        self.assertIn("not imported or activated", text)
        self.assertIn("EnvironmentFile=/etc/caddy/arena-auth.env", text)
        self.assertIn("caddy validate --config /etc/caddy/Caddyfile", text)
        self.assertIn("Rollback", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
