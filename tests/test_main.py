import asyncio
import json
import logging
import os
import signal
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from proxy.main import _configure_logging, _timing_callback, config_from_options, run

BASE = {
    "allowed_sources": ["192.0.2.10"],
    "expected_paths": ["/central/central"],
    "upstream_path": "/central/central",
    "charge_point_id": "central",
    "upstream_port": 9000,
    "max_message_bytes": 65536,
    "log_level": "info",
}
AUTO = {**BASE, "expected_paths": [], "upstream_path": "", "charge_point_id": "auto"}


class OptionsTests(unittest.TestCase):
    def test_missing_supervisor_token_keeps_relay_available(self):
        logger = logging.getLogger("ocular_ocpp_websocket_proxy")
        with self.assertLogs(logger, level="ERROR") as captured:
            callback = _timing_callback({})
        self.assertIsNone(callback)
        self.assertIn("timing_disabled", "\n".join(captured.output))

    def test_debug_mode_keeps_websocket_transport_logs_secret_safe(self):
        root = logging.getLogger()
        websocket_logger = logging.getLogger("websockets")
        previous_root_level = root.level
        previous_handlers = list(root.handlers)
        previous_websocket_level = websocket_logger.level
        try:
            _configure_logging("DEBUG")
            self.assertEqual(websocket_logger.level, logging.WARNING)
        finally:
            root.handlers[:] = previous_handlers
            root.setLevel(previous_root_level)
            websocket_logger.setLevel(previous_websocket_level)

    def test_builds_bounded_fail_closed_config(self):
        config = config_from_options(BASE)
        self.assertEqual(config.listen_host, "0.0.0.0")
        self.assertEqual(config.listen_port, 9000)
        self.assertEqual(config.upstream_base, "ws://homeassistant:9000")
        self.assertEqual(config.allowed_sources, ("192.0.2.10",))
        self.assertEqual(config.expected_paths, ("/central/central",))
        self.assertEqual(config.upstream_path, "/central/central")
        self.assertEqual(config.charge_point_id, "central")
        self.assertEqual(config.max_message_bytes, 65536)

    def test_auto_charge_point_id_learns_from_the_charger(self):
        for options in (
            AUTO,
            {**AUTO, "expected_paths": None, "upstream_path": None},
            {k: v for k, v in AUTO.items() if k not in ("expected_paths", "upstream_path")},
            {k: v for k, v in AUTO.items() if k != "charge_point_id"},
        ):
            with self.subTest(options=options):
                config = config_from_options(options)
                self.assertTrue(config.learns_charge_point_id)
                self.assertIsNone(config.charge_point_id)
                self.assertIsNone(config.upstream_path)
                self.assertEqual(config.expected_paths, ())

    def test_auto_charge_point_id_rejects_pinned_paths(self):
        with self.assertRaisesRegex(ValueError, "auto"):
            config_from_options({**AUTO, "expected_paths": ["/central/central"]})
        with self.assertRaisesRegex(ValueError, "auto"):
            config_from_options({**AUTO, "upstream_path": "/central/central"})

    def test_pinned_charge_point_id_requires_paths(self):
        with self.assertRaises(ValueError):
            config_from_options({**BASE, "expected_paths": []})
        with self.assertRaises(ValueError):
            config_from_options({**BASE, "upstream_path": ""})

    def test_accepts_a_bounded_custom_home_assistant_ocpp_port(self):
        config = config_from_options({**BASE, "upstream_port": 9100})
        self.assertEqual(config.upstream_base, "ws://homeassistant:9100")

    def test_rejects_invalid_home_assistant_ocpp_ports(self):
        for port in (0, 65536, True, "9000"):
            with self.subTest(port=port), self.assertRaises(ValueError):
                config_from_options({**BASE, "upstream_port": port})

    def test_rejects_empty_or_invalid_source_allowlist(self):
        for sources in (
            [],
            ["not-an-ip"],
            "192.0.2.10",
            ["192.0.2.5/24"],
            ["10.0.0.0/8"],
            ["0.0.0.0/0"],
            ["fd00::/48"],
            ["192.0.2.0/24", "192.0.2.0/24"],
        ):
            with self.subTest(sources=sources), self.assertRaises(ValueError):
                config_from_options({**BASE, "allowed_sources": sources})

    def test_accepts_cidr_source_networks(self):
        config = config_from_options(
            {**BASE, "allowed_sources": ["192.0.2.0/24", "192.0.2.10", "fd00::/64"]}
        )
        self.assertEqual(
            config.allowed_sources, ("192.0.2.0/24", "192.0.2.10", "fd00::/64")
        )
        self.assertTrue(config.allows_source("192.0.2.77"))
        self.assertFalse(config.allows_source("192.0.3.1"))

    def test_rejects_unbounded_or_duplicate_paths(self):
        with self.assertRaises(ValueError):
            config_from_options({**BASE, "expected_paths": []})
        with self.assertRaises(ValueError):
            config_from_options(
                {**BASE, "expected_paths": [f"/p/{i}" for i in range(9)]}
            )
        with self.assertRaises(ValueError):
            config_from_options(
                {**BASE, "expected_paths": ["/central/central", "/central/central"]}
            )

    def test_rejects_upstream_path_that_changes_ha_charge_point_identity(self):
        with self.assertRaisesRegex(ValueError, "charge-point identity"):
            config_from_options({**BASE, "upstream_path": "/central/other"})

    def test_rejects_non_canonical_paths(self):
        for path in ("/central/..", "/./central", "/central/.", "/a//b", "/a/../b"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                config_from_options({**BASE, "expected_paths": [path]})

    def test_rejects_unbounded_message_size(self):
        for size in (0, 1023, 1048577, True):
            with self.subTest(size=size), self.assertRaises(ValueError):
                config_from_options({**BASE, "max_message_bytes": size})


class ShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def test_sigterm_stops_relay_cleanly(self):
        relay = AsyncMock()
        with tempfile.TemporaryDirectory() as directory:
            options_path = Path(directory) / "options.json"
            options_path.write_text(json.dumps(AUTO), encoding="utf-8")
            logger = logging.getLogger("ocular_ocpp_websocket_proxy")
            with (
                patch("proxy.main._configure_logging"),
                patch("proxy.main._drop_privileges"),
                patch("proxy.main.OcppWebSocketRelay", return_value=relay),
                self.assertLogs(logger, level="INFO") as captured,
            ):
                runner = asyncio.create_task(run(options_path))
                while not relay.start.await_count:
                    await asyncio.sleep(0)
                os.kill(os.getpid(), signal.SIGTERM)
                await asyncio.wait_for(runner, 2)
        relay.start.assert_awaited_once()
        relay.stop.assert_awaited_once()
        output = "\n".join(captured.output)
        self.assertIn("shutdown_requested signal=SIGTERM", output)
        self.assertIn("stopped", output)
        self.assertEqual(signal.getsignal(signal.SIGTERM), signal.SIG_DFL)


if __name__ == "__main__":
    unittest.main()
