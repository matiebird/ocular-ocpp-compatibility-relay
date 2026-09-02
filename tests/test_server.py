import asyncio
import logging
import unittest
from unittest.mock import AsyncMock, patch

import websockets
from websockets.exceptions import ConnectionClosedError, InvalidStatusCode

from proxy.server import OcppWebSocketRelay, RelayConfig, _safe_log_path


class RelayIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.upstream_connections = []
        self.upstream_paths = []
        self.upstream_messages = []

        async def upstream_handler(websocket, path):
            self.upstream_connections.append(websocket)
            self.upstream_paths.append(path)
            try:
                async for message in websocket:
                    self.upstream_messages.append(message)
                    await websocket.send(b"upstream:" + message if isinstance(message, bytes) else "upstream:" + message)
            except websockets.ConnectionClosed:
                pass

        self.upstream_server = await websockets.serve(
            upstream_handler,
            "127.0.0.1",
            0,
            subprotocols=["ocpp1.6"],
            ping_interval=None,
            compression=None,
            max_size=None,
        )
        self.upstream_port = self.upstream_server.sockets[0].getsockname()[1]

    async def asyncTearDown(self):
        self.upstream_server.close()
        await self.upstream_server.wait_closed()

    async def start_relay(self, **overrides):
        values = {
            "listen_host": "127.0.0.1",
            "listen_port": 0,
            "upstream_base": f"ws://127.0.0.1:{self.upstream_port}",
            "upstream_path": "/central/central",
            "charge_point_id": "central",
            "allowed_sources": ("127.0.0.1",),
            "expected_paths": ("/central/central",),
            "max_message_bytes": 1024,
        }
        values.update(overrides)
        relay = OcppWebSocketRelay(RelayConfig(**values))
        server = await relay.start()
        self.addAsyncCleanup(self.close_server, relay)
        return relay, server.sockets[0].getsockname()[1]

    async def close_server(self, relay):
        await relay.stop()

    def connect(self, port, *, path="/central/central", subprotocols=("ocpp1.6",)):
        return websockets.connect(
            f"ws://127.0.0.1:{port}{path}",
            subprotocols=list(subprotocols),
            ping_interval=None,
            compression=None,
            max_size=None,
        )

    async def test_charger_and_upstream_are_separate_sessions_and_forward_both_ways(self):
        relay, port = await self.start_relay()
        async with self.connect(port) as charger:
            await charger.send('[2,"uid","Heartbeat",{}]')
            self.assertEqual(await charger.recv(), 'upstream:[2,"uid","Heartbeat",{}]')
            await charger.send(b"binary")
            self.assertEqual(await charger.recv(), b"upstream:binary")
            self.assertIsNot(charger, self.upstream_connections[0])
        self.assertEqual(self.upstream_messages, ['[2,"uid","Heartbeat",{}]', b"binary"])
        self.assertEqual(relay.counters["forwarded_charger_messages"], 2)
        self.assertEqual(relay.counters["forwarded_upstream_messages"], 2)

    async def test_upstream_path_is_configured_and_keeps_charge_point_identity(self):
        _, port = await self.start_relay(upstream_path="/ha/central")
        async with self.connect(port) as charger:
            await charger.send("hello")
            await charger.recv()
        self.assertEqual(self.upstream_paths, ["/ha/central"])

    async def test_source_path_and_subprotocol_rejections_fail_closed(self):
        relay, port = await self.start_relay(allowed_sources=("192.0.2.1",))
        with self.assertRaises((InvalidStatusCode, ConnectionClosedError)):
            async with self.connect(port):
                pass
        self.assertEqual(relay.counters["rejected_source"], 1)
        self.assertEqual(self.upstream_connections, [])

        relay2, port2 = await self.start_relay()
        with self.assertRaises((InvalidStatusCode, ConnectionClosedError)):
            async with self.connect(port2, path="/wrong"):
                pass
        self.assertEqual(relay2.counters["rejected_path"], 1)

        relay3, port3 = await self.start_relay()
        with self.assertRaises((InvalidStatusCode, ConnectionClosedError)):
            async with self.connect(port3, subprotocols=("chat",)):
                pass
        self.assertEqual(relay3.counters["rejected_subprotocol"], 1)

    async def test_upstream_failure_closes_charger_without_retry(self):
        probe = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
        dead_port = probe.sockets[0].getsockname()[1]
        probe.close()
        await probe.wait_closed()
        relay, port = await self.start_relay(upstream_base=f"ws://127.0.0.1:{dead_port}")
        async with self.connect(port) as charger:
            with self.assertRaises(ConnectionClosedError) as raised:
                await charger.recv()
            self.assertEqual(raised.exception.code, 1011)
        self.assertEqual(relay.counters["upstream_failures"], 1)
        self.assertEqual(relay.counters["upstream_connections"], 0)

    async def test_upstream_failure_after_open_closes_charger(self):
        _, port = await self.start_relay()
        async with self.connect(port) as charger:
            while not self.upstream_connections:
                await asyncio.sleep(0)
            await self.upstream_connections[0].close(code=1011, reason="backend failed")
            with self.assertRaises(ConnectionClosedError) as raised:
                await asyncio.wait_for(charger.recv(), 0.5)
            self.assertEqual(raised.exception.code, 1011)

    async def test_control_ping_terminates_locally_and_is_not_an_ocpp_message(self):
        _, port = await self.start_relay()
        async with self.connect(port) as charger:
            pong = await charger.ping(b"private-ping")
            await asyncio.wait_for(pong, 1)
            await asyncio.sleep(0.05)
            self.assertEqual(self.upstream_messages, [])

    async def test_oversized_message_closes_without_forwarding(self):
        relay, port = await self.start_relay(max_message_bytes=8)
        logger = logging.getLogger("ocular_ocpp_websocket_proxy")
        with self.assertLogs(logger, level="INFO") as captured:
            async with self.connect(port) as charger:
                await charger.send("ninebytes")
                with self.assertRaises(ConnectionClosedError) as raised:
                    await charger.recv()
                self.assertEqual(raised.exception.code, 1009)
            await asyncio.sleep(0.05)
        self.assertEqual(self.upstream_messages, [])
        self.assertEqual(relay.counters["oversized_messages"], 1)
        output = "\n".join(captured.output)
        self.assertIn("cause=message_too_large", output)
        self.assertIn("charger_code=1009", output)

    async def test_rejected_path_debug_log_removes_query_secrets(self):
        relay, port = await self.start_relay()
        logger = logging.getLogger("ocular_ocpp_websocket_proxy")
        with self.assertLogs(logger, level="DEBUG") as captured:
            with self.assertRaises((InvalidStatusCode, ConnectionClosedError)):
                async with self.connect(port, path="/wrong?token=TOP-SECRET"):
                    pass
        output = "\n".join(captured.output)
        self.assertIn("path='/wrong'", output)
        self.assertNotIn("TOP-SECRET", output)
        self.assertNotIn("token", output)
        self.assertEqual(relay.counters["rejected_path"], 1)

    def test_safe_log_path_escapes_control_characters(self):
        rendered = _safe_log_path("/wrong\x1b[31m\r\nnext?token=TOP-SECRET")
        self.assertIn("\\x1b", rendered)
        self.assertNotIn("\x1b", rendered)
        self.assertNotIn("\r", rendered)
        self.assertNotIn("\n", rendered)
        self.assertNotIn("TOP-SECRET", rendered)

    async def test_connection_log_records_charger_close_cause(self):
        _, port = await self.start_relay()
        logger = logging.getLogger("ocular_ocpp_websocket_proxy")
        with self.assertLogs(logger, level="INFO") as captured:
            async with self.connect(port) as charger:
                await charger.send("hello")
                await charger.recv()
            await asyncio.sleep(0.05)
        output = "\n".join(captured.output)
        self.assertIn("cause=charger_close", output)
        self.assertIn("charger_code=1000", output)

    async def test_connection_log_records_upstream_normal_close_cause(self):
        _, port = await self.start_relay()
        logger = logging.getLogger("ocular_ocpp_websocket_proxy")
        with self.assertLogs(logger, level="INFO") as captured:
            async with self.connect(port) as charger:
                while not self.upstream_connections:
                    await asyncio.sleep(0)
                await self.upstream_connections[0].close(code=1000, reason="done")
                with self.assertRaises(websockets.ConnectionClosed) as raised:
                    await charger.recv()
                self.assertEqual(raised.exception.code, 1001)
            await asyncio.sleep(0.05)
        output = "\n".join(captured.output)
        self.assertIn("cause=upstream_close", output)
        self.assertIn("upstream_code=1000", output)

    async def test_connection_log_records_upstream_failure_cause(self):
        _, port = await self.start_relay()
        logger = logging.getLogger("ocular_ocpp_websocket_proxy")
        with self.assertLogs(logger, level="INFO") as captured:
            async with self.connect(port) as charger:
                while not self.upstream_connections:
                    await asyncio.sleep(0)
                await self.upstream_connections[0].close(code=1011, reason="failed")
                with self.assertRaises(ConnectionClosedError) as raised:
                    await charger.recv()
                self.assertEqual(raised.exception.code, 1011)
            await asyncio.sleep(0.05)
        output = "\n".join(captured.output)
        self.assertIn("cause=upstream_failure", output)
        self.assertIn("upstream_code=1011", output)

    async def test_connection_log_records_relay_shutdown_cause(self):
        relay, port = await self.start_relay()
        logger = logging.getLogger("ocular_ocpp_websocket_proxy")
        with self.assertLogs(logger, level="INFO") as captured:
            charger = await self.connect(port)
            self.addAsyncCleanup(charger.close)
            while not self.upstream_connections:
                await asyncio.sleep(0)
            await relay.stop()
            await asyncio.sleep(0.05)
        output = "\n".join(captured.output)
        self.assertIn("cause=relay_shutdown", output)
        self.assertIn("charger_code=1001", output)

    async def test_connection_log_records_initial_upstream_failure(self):
        probe = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
        dead_port = probe.sockets[0].getsockname()[1]
        probe.close()
        await probe.wait_closed()
        logger = logging.getLogger("ocular_ocpp_websocket_proxy")
        with self.assertLogs(logger, level="INFO") as captured:
            _, port = await self.start_relay(
                upstream_base=f"ws://127.0.0.1:{dead_port}"
            )
            async with self.connect(port) as charger:
                with self.assertRaises(ConnectionClosedError):
                    await charger.recv()
            await asyncio.sleep(0.05)
        output = "\n".join(captured.output)
        self.assertIn("cause=upstream_failure", output)
        self.assertIn("upstream_code=None", output)

    async def test_logs_do_not_contain_raw_credentials_or_frames(self):
        relay, port = await self.start_relay()
        logger = logging.getLogger("ocular_ocpp_websocket_proxy")
        with self.assertLogs(logger, level="INFO") as captured:
            async with self.connect(port) as charger:
                secret_frame = '[2,"credential-uid","Authorize",{"idTag":"TOP-SECRET"}]'
                await charger.send(secret_frame)
                await charger.recv()
            await asyncio.sleep(0.05)
        output = "\n".join(captured.output)
        self.assertNotIn("TOP-SECRET", output)
        self.assertNotIn("credential-uid", output)
        self.assertNotIn(secret_frame, output)
        self.assertIn("connection_open", output)
        self.assertIn("connection_closed", output)
        self.assertEqual(relay.counters["forwarded_charger_messages"], 1)


class ServerConfigurationTests(unittest.IsolatedAsyncioTestCase):
    async def test_server_uses_omv_compatible_websocket_options_and_ten_second_open_timeout(self):
        config = RelayConfig(
            listen_host="127.0.0.1",
            listen_port=9000,
            upstream_base="ws://homeassistant:9001",
            upstream_path="/central/central",
            charge_point_id="central",
            allowed_sources=("127.0.0.1",),
            expected_paths=("/central/central",),
            max_message_bytes=65536,
        )
        fake_server = AsyncMock()
        with patch("proxy.server.websockets.serve", new=AsyncMock(return_value=fake_server)) as serve:
            relay = OcppWebSocketRelay(config)
            await relay.start()
        kwargs = serve.await_args.kwargs
        self.assertEqual(kwargs["subprotocols"], ["ocpp1.6"])
        self.assertIsNone(kwargs["ping_interval"])
        self.assertIsNone(kwargs["compression"])
        self.assertIsNone(kwargs["max_size"])
        self.assertEqual(kwargs["open_timeout"], 10)
        self.assertLessEqual(kwargs["read_limit"], 65536)
        self.assertLessEqual(kwargs["max_queue"], 32)
