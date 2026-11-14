import unittest
from types import SimpleNamespace

from model_permission_auth import authenticated_actor


class ModelPermissionAuthTests(unittest.TestCase):
    def test_http_identity_headers_are_not_trusted(self):
        handler = SimpleNamespace(headers={
            "Authorization": "Basic YXJlbmE6cGFzcw==",
            "X-Authenticated-User": "admin",
            "X-Forwarded-User": "root",
        })
        self.assertIsNone(authenticated_actor(handler))

    def test_untrusted_json_or_peer_fields_are_not_identity_sources(self):
        handler = SimpleNamespace(payload={"actor": "admin"}, client_address=("127.0.0.1", 1234))
        self.assertIsNone(authenticated_actor(handler))

    def test_only_in_process_verified_actor_attribute_is_accepted(self):
        handler = SimpleNamespace(_arena_verified_actor="operator@example.test")
        self.assertEqual(authenticated_actor(handler), "operator@example.test")

    def test_malformed_verified_actor_is_rejected(self):
        for actor in ("", "root\\n", "x" * 129, None):
            with self.subTest(actor=actor):
                handler = SimpleNamespace(_arena_verified_actor=actor)
                self.assertIsNone(authenticated_actor(handler))


if __name__ == "__main__":
    unittest.main()
