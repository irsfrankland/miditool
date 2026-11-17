"""The local Chrome bridge: who may talk to it, and what it keeps."""

from __future__ import annotations

import http.client
import json
import socket
import unittest

from tests import support  # noqa: F401

from chrome_bridge import ChromeBridge


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def post(port, path="/songsterr", body=b'{"playing": true}', headers=None, host=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    send = {"Content-Type": "application/json"}
    send.update(headers or {})
    connection.putrequest("POST", path, skip_host=True)
    connection.putheader("Host", host or f"127.0.0.1:{port}")
    for key, value in send.items():
        connection.putheader(key, value)
    connection.putheader("Content-Length", str(len(body)))
    connection.endheaders(body)
    response = connection.getresponse()
    data = response.read()
    connection.close()
    return response.status, data


class Bridge(unittest.TestCase):
    def setUp(self):
        self.port = free_port()
        self.bridge = ChromeBridge(self.port)
        self.assertEqual(self.bridge.error, "")

    def tearDown(self):
        self.bridge.close()

    def test_state_and_command_roundtrip(self):
        self.assertEqual(self.bridge.latest(), (None, float("inf")))
        self.bridge.send("pause")
        status, data = post(self.port, headers={"Origin": "chrome-extension://abc"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data), {"command": "pause"})
        state, age = self.bridge.latest()
        self.assertEqual(state, {"playing": True})
        self.assertLess(age, 2)
        # the command is delivered once
        _s, data = post(self.port)
        self.assertEqual(json.loads(data), {"command": None})

    def test_web_page_origin_is_refused(self):
        status, _ = post(self.port, headers={"Origin": "https://evil.example"})
        self.assertEqual(status, 403)
        self.assertIsNone(self.bridge.latest()[0])

    def test_dns_rebinding_host_is_refused(self):
        status, _ = post(self.port, host="evil.example:%d" % self.port)
        self.assertEqual(status, 403)

    def test_localhost_host_is_allowed(self):
        status, _ = post(self.port, host=f"localhost:{self.port}")
        self.assertEqual(status, 200)

    def test_wrong_path(self):
        status, _ = post(self.port, path="/other")
        self.assertEqual(status, 404)

    def test_bad_json(self):
        status, _ = post(self.port, body=b"{oops")
        self.assertEqual(status, 400)

    def test_non_object_json_is_accepted_as_empty(self):
        status, _ = post(self.port, body=b"[1,2,3]")
        self.assertEqual(status, 200)
        self.assertEqual(self.bridge.latest()[0], {})

    def test_huge_body_is_capped(self):
        status, _ = post(self.port, body=b'{"a": "' + b"x" * 200_000 + b'"}')
        self.assertIn(status, (400, 200))

    def test_second_bridge_reports_busy_port(self):
        second = ChromeBridge(self.port)
        try:
            self.assertIn("busy", second.error)
        finally:
            second.close()

    def test_close_is_idempotent(self):
        self.bridge.close()
        self.bridge.close()


if __name__ == "__main__":
    unittest.main()
