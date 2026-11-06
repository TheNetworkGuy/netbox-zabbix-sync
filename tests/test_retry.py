"""Tests for retrying NetBox and Zabbix API calls on transient failures."""

import json
import ssl
import threading
import unittest
from email.message import Message
from http.client import RemoteDisconnected
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from pynetbox.core.query import RequestError
from requests.adapters import HTTPAdapter
from zabbix_utils import APIRequestError, ProcessingError, ZabbixAPI

from netbox_zabbix_sync.modules.api_clients import (
    HTTP_RETRIES,
    NETBOX_RETRY,
    RetryingNetBoxAPI,
    RetryingZabbixAPI,
)

ATTEMPTS = HTTP_RETRIES + 1
OK = {"jsonrpc": "2.0", "result": "ok", "id": "1"}


def processing_error(cause: Exception) -> ProcessingError:
    """Build a ProcessingError the way zabbix_utils raises it: `from None`."""
    try:
        try:
            raise cause
        except Exception as err:
            raise ProcessingError("Unable to connect:", err) from None
    except ProcessingError as error:
        return error


def http_error(code: int) -> ProcessingError:
    return processing_error(HTTPError("http://zabbix", code, "error", Message(), None))


def url_error(reason: Exception) -> ProcessingError:
    return processing_error(URLError(reason))


def bare_zabbix() -> RetryingZabbixAPI:
    """A RetryingZabbixAPI without the version check and login of __init__."""
    api = RetryingZabbixAPI.__new__(RetryingZabbixAPI)
    api.url = "http://zabbix.local/api_jsonrpc.php"
    api.validate_certs = True
    api.ssl_context = None
    api.timeout = 5
    return api


class TestZabbixRetryDecisions(unittest.TestCase):
    """Which failures are retried, for which methods, and how often."""

    def setUp(self):
        self.api = bare_zabbix()
        send = patch.object(ZabbixAPI, "send_api_request")
        self.send = send.start()
        self.addCleanup(send.stop)
        sleep = patch("tenacity.nap.time.sleep")
        self.sleep = sleep.start()
        self.addCleanup(sleep.stop)

    def call(self, method: str = "host.get"):
        return self.api.send_api_request(method, {}, need_auth=False)

    def test_success_needs_no_retry(self):
        self.send.return_value = OK
        self.assertEqual(self.call(), OK)
        self.assertEqual(self.send.call_count, 1)
        self.sleep.assert_not_called()

    def test_recovers_after_transient_503(self):
        self.send.side_effect = [http_error(503), http_error(503), OK]
        with self.assertLogs("NetBox-Zabbix-sync", level="WARNING") as logs:
            self.assertEqual(self.call(), OK)
        self.assertEqual(self.send.call_count, 3)
        self.assertIn("HTTP 503", logs.output[0])
        self.assertIn("retry 1 of 2", logs.output[0])

    def test_gives_up_after_three_attempts(self):
        self.send.side_effect = [http_error(502)] * 5
        with self.assertRaises(ProcessingError), self.assertLogs("NetBox-Zabbix-sync"):
            self.call()
        self.assertEqual(self.send.call_count, ATTEMPTS)

    def test_exponential_backoff(self):
        self.send.side_effect = [http_error(504)] * ATTEMPTS
        with self.assertRaises(ProcessingError), self.assertLogs("NetBox-Zabbix-sync"):
            self.call()
        delays = [c.args[0] for c in self.sleep.call_args_list]
        self.assertEqual(delays, [1, 2])

    def test_other_status_codes_are_not_retried(self):
        for code in (400, 401, 403, 404, 500):
            with self.subTest(code=code):
                self.send.reset_mock()
                self.send.side_effect = [http_error(code), OK]
                with self.assertRaises(ProcessingError):
                    self.call()
                self.assertEqual(self.send.call_count, 1)

    def test_5xx_not_retried_for_create_and_delete(self):
        """Zabbix may have processed the request, so a retry could fail falsely."""
        for method in ("host.create", "hostinterface.delete"):
            with self.subTest(method=method):
                self.send.reset_mock()
                self.send.side_effect = [http_error(503), OK]
                with self.assertRaises(ProcessingError):
                    self.call(method)
                self.assertEqual(self.send.call_count, 1)

    def test_5xx_retried_for_update(self):
        self.send.side_effect = [http_error(503), OK]
        with self.assertLogs("NetBox-Zabbix-sync"):
            self.assertEqual(self.call("host.update"), OK)

    def test_connect_errors_retried_for_any_method(self):
        """The request never reached Zabbix, so even create is safe to resend."""
        for reason in (
            ConnectionRefusedError(111, "Connection refused"),
            TimeoutError("timed out"),
        ):
            with self.subTest(reason=reason):
                self.send.reset_mock()
                self.send.side_effect = [url_error(reason), OK]
                with self.assertLogs("NetBox-Zabbix-sync"):
                    self.assertEqual(self.call("host.create"), OK)
                self.assertEqual(self.send.call_count, 2)

    def test_other_url_errors_retried_only_for_idempotent_methods(self):
        reset = ConnectionResetError(104, "Connection reset by peer")
        self.send.side_effect = [url_error(reset), OK]
        with self.assertLogs("NetBox-Zabbix-sync"):
            self.assertEqual(self.call("host.get"), OK)

        self.send.reset_mock()
        self.send.side_effect = [url_error(reset), OK]
        with self.assertRaises(ProcessingError):
            self.call("host.create")
        self.assertEqual(self.send.call_count, 1)

    def test_certificate_errors_are_not_retried(self):
        self.send.side_effect = [url_error(ssl.SSLCertVerificationError("bad")), OK]
        with self.assertRaises(ProcessingError):
            self.call()
        self.assertEqual(self.send.call_count, 1)

    def test_invalid_json_is_not_retried(self):
        self.send.side_effect = [processing_error(ValueError("bad json")), OK]
        with self.assertRaises(ProcessingError):
            self.call()
        self.assertEqual(self.send.call_count, 1)

    def test_api_errors_are_not_retried(self):
        self.send.side_effect = [APIRequestError("No permissions"), OK]
        with self.assertRaises(APIRequestError):
            self.call()
        self.assertEqual(self.send.call_count, 1)

    def test_unwrapped_read_errors_retried_only_for_idempotent_methods(self):
        """zabbix_utils lets errors while reading the response through as-is."""
        for error in (TimeoutError("timed out"), RemoteDisconnected("closed")):
            with self.subTest(error=error):
                self.send.reset_mock()
                self.send.side_effect = [error, OK]
                with self.assertLogs("NetBox-Zabbix-sync"):
                    self.assertEqual(self.call("host.get"), OK)

                self.send.reset_mock()
                self.send.side_effect = [error, OK]
                with self.assertRaises(type(error)):
                    self.call("host.delete")
                self.assertEqual(self.send.call_count, 1)


class FlakyServer:
    """Local HTTP server that answers 503 a given number of times, then 200."""

    def __init__(self, body: dict):
        self.failures = 0
        self.requests: list[str] = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def _reply(self):
                length = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(length)
                server.requests.append(self.command)
                if server.failures > 0:
                    server.failures -= 1
                    self.send_response(503)
                    self.end_headers()
                    return
                payload = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_GET = do_POST = do_PATCH = _reply  # noqa: N815

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class TestZabbixRetryOverHTTP(unittest.TestCase):
    """End-to-end through the real urllib code path of zabbix_utils."""

    def setUp(self):
        self.server = FlakyServer(OK)
        self.addCleanup(self.server.close)
        self.api = bare_zabbix()
        self.api.url = self.server.url
        sleep = patch("tenacity.nap.time.sleep")
        sleep.start()
        self.addCleanup(sleep.stop)

    def test_recovers_from_503(self):
        self.server.failures = 2
        with self.assertLogs("NetBox-Zabbix-sync", level="WARNING"):
            result = self.api.send_api_request("host.get", {}, need_auth=False)
        self.assertEqual(result["result"], "ok")
        self.assertEqual(len(self.server.requests), 3)

    def test_gives_up_with_processing_error(self):
        self.server.failures = 10
        with self.assertRaises(ProcessingError), self.assertLogs("NetBox-Zabbix-sync"):
            self.api.send_api_request("host.get", {}, need_auth=False)
        self.assertEqual(len(self.server.requests), ATTEMPTS)

    def test_create_is_sent_once(self):
        self.server.failures = 1
        with self.assertRaises(ProcessingError):
            self.api.send_api_request("host.create", {}, need_auth=False)
        self.assertEqual(len(self.server.requests), 1)


class TestNetboxRetry(unittest.TestCase):
    """The NetBox session retries through the urllib3 Retry on its adapter."""

    def test_constructor_mounts_retry_adapter(self):
        netbox = RetryingNetBoxAPI("https://netbox.local", token="token")
        for url in ("http://netbox.local/api/", "https://netbox.local/api/"):
            with self.subTest(url=url):
                adapter = netbox.http_session.get_adapter(url)
                assert isinstance(adapter, HTTPAdapter)
                self.assertIs(adapter.max_retries, NETBOX_RETRY)

    def test_policy(self):
        self.assertEqual(NETBOX_RETRY.total, HTTP_RETRIES)
        self.assertFalse(NETBOX_RETRY.raise_on_status)
        self.assertTrue(NETBOX_RETRY.is_retry("GET", 503))
        self.assertTrue(NETBOX_RETRY.is_retry("PATCH", 503))
        self.assertFalse(NETBOX_RETRY.is_retry("POST", 503))
        self.assertFalse(NETBOX_RETRY.is_retry("GET", 500))


class TestNetboxRetryOverHTTP(unittest.TestCase):
    """End-to-end through pynetbox, requests and urllib3."""

    def setUp(self):
        self.server = FlakyServer({"id": 1, "name": "sw1"})
        self.addCleanup(self.server.close)
        self.nb = RetryingNetBoxAPI(self.server.url, token="token")
        sleep = patch("urllib3.util.retry.time.sleep")
        sleep.start()
        self.addCleanup(sleep.stop)

    def test_get_recovers_from_503(self):
        self.server.failures = 2
        self.assertEqual(self.nb.dcim.devices.get(1).name, "sw1")
        self.assertEqual(self.server.requests, ["GET"] * 3)

    def test_exhausted_retries_raise_pynetbox_request_error(self):
        """Not requests' RetryError, which the sync does not handle."""
        self.server.failures = 10
        with self.assertRaises(RequestError):
            self.nb.dcim.devices.get(1)
        self.assertEqual(len(self.server.requests), ATTEMPTS)

    def test_patch_is_retried(self):
        device = self.nb.dcim.devices.get(1)
        self.server.requests.clear()
        self.server.failures = 1
        device.name = "sw2"
        self.assertTrue(device.save())
        self.assertEqual(self.server.requests, ["PATCH", "PATCH"])

    def test_post_is_not_retried(self):
        self.server.failures = 1
        with self.assertRaises(RequestError):
            self.nb.dcim.devices.create(name="sw2")
        self.assertEqual(self.server.requests, ["POST"])


if __name__ == "__main__":
    unittest.main()
