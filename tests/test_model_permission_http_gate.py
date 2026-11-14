import unittest
from types import SimpleNamespace

from server import Handler


class UnreadableBody:
    def read(self, *_args, **_kwargs):
        raise AssertionError("permission request body must not be read before authentication")


class ModelPermissionHttpGateTests(unittest.TestCase):
    def test_all_permission_mutations_return_403_before_reading_body(self):
        paths = (
            "/api/model-permissions/request",
            "/api/model-permissions/approve",
            "/api/model-permissions/reject",
            "/api/model-permissions/revoke",
        )
        for path in paths:
            with self.subTest(path=path):
                responses = []
                handler = SimpleNamespace(
                    path=path,
                    headers={
                        "Authorization": "Basic YXJlbmE6cGFzcw==",
                        "X-Authenticated-User": "admin",
                        "X-Forwarded-User": "root",
                    },
                    rfile=UnreadableBody(),
                    send_json=lambda payload, code=200: responses.append((payload, code)),
                )
                Handler.do_POST(handler)
                self.assertEqual(len(responses), 1)
                self.assertEqual(responses[0][1], 403)
                self.assertIn("tożsamości operatora", responses[0][0]["error"])


if __name__ == "__main__":
    unittest.main()
