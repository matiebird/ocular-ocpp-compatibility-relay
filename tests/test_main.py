import logging
import unittest

from proxy.main import _configure_logging, _timing_callback, config_from_options

BASE = {
    "allowed_sources": ["192.0.2.10"],
    "expected_paths": ["/central/central"],
    "upstream_path": "/central/central",
    "charge_point_id": "central",
    "upstream_port": 9000,
    "max_message_bytes": 65536,
    "log_level": "info",
}


class OptionsTests(unittest.TestCase):
    def test_missing_supervisor_token_keeps_relay_available(self):
        config = config_from_options(BASE)
        logger = logging.getLogger("ocular_ocpp_websocket_proxy")
        with self.assertLogs(logger, level="ERROR") as captured:
            callback = _timing_callback(config, {})
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

    def test_accepts_a_bounded_custom_home_assistant_ocpp_port(self):
        config = config_from_options({**BASE, "upstream_port": 9100})
        self.assertEqual(config.upstream_base, "ws://homeassistant:9100")

    def test_rejects_invalid_home_assistant_ocpp_ports(self):
        for port in (0, 65536, True, "9000"):
            with self.subTest(port=port), self.assertRaises(ValueError):
                config_from_options({**BASE, "upstream_port": port})

    def test_rejects_empty_or_invalid_source_allowlist(self):
        for sources in ([], ["not-an-ip"], "192.0.2.10"):
            with self.subTest(sources=sources), self.assertRaises(ValueError):
                config_from_options({**BASE, "allowed_sources": sources})

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

    def test_rejects_unbounded_message_size(self):
        for size in (0, 1023, 1048577, True):
            with self.subTest(size=size), self.assertRaises(ValueError):
                config_from_options({**BASE, "max_message_bytes": size})


if __name__ == "__main__":
    unittest.main()
