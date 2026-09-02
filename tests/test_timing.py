import asyncio
import unittest
from http.client import RemoteDisconnected
from io import BytesIO
from unittest.mock import patch

from proxy.timing import (
    HomeAssistantTimingClient,
    TimingController,
    TimingVerificationError,
)


class HomeAssistantTimingClientTests(unittest.TestCase):
    def test_configures_all_values_and_requires_matching_readback(self):
        calls = []
        expected = {
            "HeartbeatInterval": "60",
            "WebSocketPingInterval": "60",
            "MeterValueSampleInterval": "10",
        }

        def request_json(path, payload):
            calls.append((path, payload))
            if path.endswith("/configure?return_response"):
                return {
                    "changed_states": [],
                    "service_response": {"reboot_required": False},
                }
            return {
                "changed_states": [],
                "service_response": {"value": expected[payload["ocpp_key"]]},
            }

        client = HomeAssistantTimingClient(
            token="private-token", request_json=request_json
        )

        self.assertEqual(client.apply_and_verify("ocular"), expected)
        self.assertEqual(len(calls), 6)
        for key, value in expected.items():
            self.assertIn(
                (
                    "/api/services/ocpp/configure?return_response",
                    {"devid": "ocular", "ocpp_key": key, "value": value},
                ),
                calls,
            )
            self.assertIn(
                (
                    "/api/services/ocpp/get_configuration?return_response",
                    {"devid": "ocular", "ocpp_key": key},
                ),
                calls,
            )

    def test_rejects_configure_reboot_requirement(self):
        expected = {
            "HeartbeatInterval": "60",
            "WebSocketPingInterval": "60",
            "MeterValueSampleInterval": "10",
        }

        def request_json(path, payload):
            if path.endswith("/configure?return_response"):
                return {
                    "changed_states": [],
                    "service_response": {"reboot_required": True},
                }
            return {
                "changed_states": [],
                "service_response": {"value": expected[payload["ocpp_key"]]},
            }

        client = HomeAssistantTimingClient(
            token="private-token", request_json=request_json
        )
        with self.assertRaisesRegex(TimingVerificationError, "reboot"):
            client.apply_and_verify("ocular")

    def test_rejects_missing_or_mismatched_readback(self):
        responses = iter(
            [
                {"service_response": {"reboot_required": False}},
                {"service_response": {"reboot_required": False}},
                {"service_response": {"reboot_required": False}},
                {"service_response": {"value": "3600"}},
                {"service_response": {"value": "60"}},
                {"service_response": {}},
            ]
        )
        client = HomeAssistantTimingClient(
            token="private-token",
            request_json=lambda path, payload: next(responses),
        )

        with self.assertRaisesRegex(TimingVerificationError, "HeartbeatInterval"):
            client.apply_and_verify("ocular")

    def test_requires_a_bounded_device_id_and_token(self):
        calls = []
        client = HomeAssistantTimingClient(
            token="token", request_json=lambda path, payload: calls.append(path)
        )
        for device_id in ("", "bad id", "x" * 65, None):
            with self.subTest(device_id=device_id), self.assertRaises(ValueError):
                client.apply_and_verify(device_id)
        self.assertEqual(calls, [])
        with self.assertRaises(ValueError):
            HomeAssistantTimingClient(token="")

    def test_normalizes_remote_disconnect_for_retry(self):
        client = HomeAssistantTimingClient(token="private-token")
        with patch(
            "proxy.timing.urlopen", side_effect=RemoteDisconnected("peer closed")
        ):
            with self.assertRaises(TimingVerificationError):
                client._default_request_json("/test", {})

    def test_normalizes_malformed_utf8_for_retry(self):
        client = HomeAssistantTimingClient(token="private-token")
        with patch("proxy.timing.urlopen", return_value=BytesIO(b"\xff")):
            with self.assertRaises(TimingVerificationError):
                client._default_request_json("/test", {})

    def test_does_not_hide_unrelated_value_error(self):
        client = HomeAssistantTimingClient(token="private-token")
        with patch(
            "proxy.timing.urlopen", side_effect=ValueError("programming defect")
        ):
            with self.assertRaisesRegex(ValueError, "programming defect"):
                client._default_request_json("/test", {})


class TimingControllerTests(unittest.IsolatedAsyncioTestCase):
    async def test_retries_transient_failure_then_verifies(self):
        attempts = 0
        sleeps = []

        class Client:
            def apply_and_verify(self, device_id):
                nonlocal attempts
                attempts += 1
                assert device_id == "driveway"
                if attempts == 1:
                    raise TimingVerificationError("not connected")
                return {
                    "HeartbeatInterval": "60",
                    "WebSocketPingInterval": "60",
                    "MeterValueSampleInterval": "10",
                }

        async def sleep(delay):
            sleeps.append(delay)

        controller = TimingController(
            Client(), initial_delay=5, retry_delays=(10,), sleep=sleep
        )
        result = await controller.apply_after_connection("driveway")

        self.assertEqual(attempts, 2)
        self.assertEqual(sleeps, [5, 10])
        self.assertEqual(result["HeartbeatInterval"], "60")

    async def test_stops_after_bounded_retries(self):
        class Client:
            def apply_and_verify(self, device_id):
                raise TimingVerificationError("unavailable")

        controller = TimingController(
            Client(), initial_delay=0, retry_delays=(0, 0), sleep=asyncio.sleep
        )
        with self.assertRaisesRegex(TimingVerificationError, "unavailable"):
            await controller.apply_after_connection("driveway")


if __name__ == "__main__":
    unittest.main()
