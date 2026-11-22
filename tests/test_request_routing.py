import unittest

import server


class RequestRoutingTests(unittest.TestCase):
    def test_tracking_query_does_not_change_root_route(self):
        self.assertEqual(server._route_path("/?utm_source=chatgpt.com"), "/")

    def test_functional_query_does_not_change_api_route(self):
        self.assertEqual(
            server._route_path("/api/passkey-tests?tournament_id=7"),
            "/api/passkey-tests",
        )

    def test_nested_route_with_query_keeps_path_only_for_matching(self):
        self.assertEqual(
            server._route_path("/api/tournaments/7/view?utm_medium=link"),
            "/api/tournaments/7/view",
        )


if __name__ == "__main__":
    unittest.main()
