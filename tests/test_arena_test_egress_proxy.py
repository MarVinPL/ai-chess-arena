import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).parents[1] / "scripts" / "arena_test_egress_proxy.py"
spec = importlib.util.spec_from_file_location("arena_test_egress_proxy", MODULE_PATH)
proxy = importlib.util.module_from_spec(spec)
import sys
sys.modules[spec.name] = proxy
spec.loader.exec_module(proxy)


def request(line, headers=""):
    return (line + "\r\n" + headers + "\r\n").encode("ascii")


class ConnectRequestTests(unittest.TestCase):
    def test_allows_exact_huggingface_host_and_port(self):
        parsed = proxy.parse_connect_request(request(
            "CONNECT huggingface.co:443 HTTP/1.1", "Host: huggingface.co\r\n"))
        self.assertEqual(parsed, proxy.ConnectTarget("huggingface.co", 443))

    def test_rejects_unsafe_authorities(self):
        cases = [
            "GET https://huggingface.co/ HTTP/1.1",
            "CONNECT huggingface.co:80 HTTP/1.1",
            "CONNECT huggingface.co:444 HTTP/1.1",
            "CONNECT 1.1.1.1:443 HTTP/1.1",
            "CONNECT [2606:4700:4700::1111]:443 HTTP/1.1",
            "CONNECT huggingface.co.evil.example:443 HTTP/1.1",
            "CONNECT user@huggingface.co:443 HTTP/1.1",
            "CONNECT huggingface.co:443@evil.example HTTP/1.1",
        ]
        for line in cases:
            with self.subTest(line=line), self.assertRaises(proxy.ProxyRequestError):
                proxy.parse_connect_request(request(line))

    def test_rejects_mismatched_host_header(self):
        with self.assertRaises(proxy.ProxyRequestError):
            proxy.parse_connect_request(request(
                "CONNECT huggingface.co:443 HTTP/1.1", "Host: evil.example\r\n"))

    def test_rejects_unsupported_headers(self):
        with self.assertRaises(proxy.ProxyRequestError):
            proxy.parse_connect_request(request(
                "CONNECT huggingface.co:443 HTTP/1.1", "Authorization: Bearer secret\r\n"))

    def test_rejects_non_allowlisted_host(self):
        with self.assertRaises(proxy.ProxyRequestError):
            proxy.parse_connect_request(request("CONNECT example.com:443 HTTP/1.1"))

    def test_rejects_malformed_and_oversized_headers(self):
        with self.assertRaises(proxy.ProxyRequestError):
            proxy.parse_connect_request(b"CONNECT huggingface.co:443 HTTP/1.1\r\n")
        with self.assertRaises(proxy.ProxyRequestError):
            proxy.parse_connect_request(b"X" * (proxy._MAX_HEADER + 1) + b"\r\n\r\n")

    def test_rejects_private_or_mixed_dns_answers(self):
        for value in ("127.0.0.1", "10.0.0.1", "169.254.1.1", "192.168.1.1", "::1", "fc00::1"):
            with self.subTest(ip=value), self.assertRaises(proxy.ProxyRequestError):
                proxy.validate_public_addresses([value])
        with self.assertRaises(proxy.ProxyRequestError):
            proxy.validate_public_addresses(["1.1.1.1", "127.0.0.1"])

    def test_accepts_and_deduplicates_public_dns_answers(self):
        self.assertEqual(proxy.validate_public_addresses(["1.1.1.1", "1.1.1.1"]), ["1.1.1.1"])


if __name__ == "__main__":
    unittest.main()
