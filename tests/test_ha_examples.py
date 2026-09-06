"""Offline rendering of the shipped YAML, not HA runtime/device qualification."""
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest

from jinja2 import Environment, StrictUndefined
import yaml

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = Path(os.environ.get("OCULAR_EXAMPLE", ROOT / "examples/ocular-everyday-controls.yaml"))


class States:
    def __init__(self):
        self.values = {
            "sensor.ocular_transaction_id": "123",
            "input_number.ocular_charge_current": "16",
            "switch.ocular_charge_control": "on",
            "sensor.ocular_status_connector": "SuspendedEVSE",
            "sensor.ocular_error_code_connector": "NoError",
            "sensor.ocular_heartbeat": "990",
            "sensor.ocular_current_import": "16",
        }
        self.sensor = SimpleNamespace(ocular_current_import=SimpleNamespace(last_reported=1001))

    def __call__(self, entity):
        return self.values.get(entity, "unknown")


def nodes(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from nodes(child)


class ExampleTests(unittest.TestCase):
    def setUp(self):
        self.package = yaml.safe_load(PACKAGE.read_text())
        self.scripts = self.package["script"]
        self.states = States()
        self.env = Environment(undefined=StrictUndefined)
        self.env.filters["to_json"] = json.dumps
        self.env.globals.update(
            states=self.states,
            is_state=lambda entity, value: self.states(entity) == value,
            state_attr=lambda entity, attr: 32,
            now=lambda: 1000,
            as_timestamp=lambda value, default=0: self.timestamp(value, default),
        )

    @staticmethod
    def timestamp(value, default):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def render(self, template, **context):
        return self.env.from_string(template).render(
            transaction_id="123", requested_amps=16, command_at=1000, **context
        ).strip()

    def script_nodes(self, name):
        return list(nodes(self.scripts["ocular_" + name + "_charging"]))

    def profile(self, name, amps):
        command = next(n for n in self.script_nodes(name) if n.get("action") == "ocpp.set_charge_rate")
        self.assertEqual(command["data"]["conn_id"], 1)
        self.assertNotIn("limit_amps", command["data"])
        return json.loads(self.env.from_string(command["data"]["custom_profile"]).render(transaction_id="123", requested_amps=amps))

    def test_same_owned_relative_profile_replaced_at_valid_rates(self):
        pause = self.profile("pause", 0)
        self.assertEqual(pause["chargingProfileId"], 3001)
        self.assertEqual(pause["stackLevel"], 1)
        self.assertEqual(pause["chargingProfileKind"], "Relative")
        self.assertEqual(pause["chargingProfilePurpose"], "TxProfile")
        self.assertEqual(pause["transactionId"], 123)
        self.assertEqual(pause["chargingSchedule"]["chargingRateUnit"], "A")
        for amps in (6, 16, 30, 32):
            resume = self.profile("resume", amps)
            self.assertEqual(resume["chargingSchedule"]["chargingSchedulePeriod"][0]["limit"], amps)
            resume["chargingSchedule"]["chargingSchedulePeriod"][0]["limit"] = 0
            self.assertEqual(pause, resume)

    def test_resume_revalidates_after_dwell(self):
        branch = self.scripts["ocular_resume_charging"]["sequence"][1]["choose"][0]["sequence"]
        guard_index = next(i for i, n in enumerate(branch) if n.get("alias") == "Revalidate manual resume authority")
        write_index = next(i for i, n in enumerate(branch) if n.get("action") == "ocpp.set_charge_rate")
        self.assertLess(guard_index, write_index)
        self.assertTrue(any(n.get("delay") == "00:00:15" for n in branch[:guard_index]))
        guard = branch[guard_index]["value_template"]
        self.assertEqual(self.render(guard), "True")
        for entity, invalid in (
            ("sensor.ocular_transaction_id", "124"),
            ("sensor.ocular_transaction_id", "0"),
            ("sensor.ocular_heartbeat", "800"),
            ("sensor.ocular_heartbeat", "1001"),
            ("switch.ocular_charge_control", "off"),
            ("sensor.ocular_error_code_connector", "GroundFailure"),
            ("sensor.ocular_status_connector", "Finishing"),
            ("sensor.ocular_status_connector", "SuspendedEV"),
            ("input_number.ocular_charge_current", "6"),
            ("input_number.ocular_charge_current", "unknown"),
        ):
            original = self.states.values[entity]
            self.states.values[entity] = invalid
            with self.subTest(entity=entity, value=invalid):
                self.assertEqual(self.render(guard), "False")
            self.states.values[entity] = original
        for rate in (0, 5, 33, 16.5, "unknown", "nan", "inf"):
            self.states.values["input_number.ocular_charge_current"] = str(rate)
            rendered = self.env.from_string(guard).render(transaction_id="123", requested_amps=rate)
            self.assertEqual(rendered.strip(), "False")
        self.states.values["input_number.ocular_charge_current"] = "16"
        self.env.globals["state_attr"] = lambda entity, attr: 10
        self.assertEqual(self.render(guard), "False")

    def test_resume_requires_new_current_same_transaction(self):
        wait = next(n["wait_template"] for n in self.script_nodes("resume") if "wait_template" in n)
        self.states.values["sensor.ocular_status_connector"] = "Charging"
        self.assertEqual(self.render(wait), "True")
        for entity, value in (("sensor.ocular_current_import", "0"), ("sensor.ocular_transaction_id", "124")):
            old = self.states.values[entity]
            self.states.values[entity] = value
            self.assertEqual(self.render(wait), "False")
            self.states.values[entity] = old
        self.states.sensor.ocular_current_import.last_reported = 999
        self.assertEqual(self.render(wait), "False")

    def test_all_repository_yaml_parses(self):
        for path in list(ROOT.glob("*.yaml")) + list((ROOT / "examples").glob("*.yaml")) + list((ROOT / ".github/workflows").glob("*.yml")):
            with self.subTest(path=path):
                yaml.safe_load(path.read_text())

    def test_no_resume_start_reset_clear_or_automatic_restart(self):
        actions = [n["action"] for n in self.script_nodes("resume") if isinstance(n.get("action"), str)]
        self.assertEqual(actions.count("ocpp.set_charge_rate"), 1)
        self.assertTrue(set(actions) <= {"ocpp.set_charge_rate", "persistent_notification.create"})
        automation_actions = [n["action"] for n in nodes(self.package["automation"]) if isinstance(n.get("action"), str)]
        self.assertNotIn("script.ocular_start_charging", automation_actions)
        self.assertNotIn("script.ocular_resume_charging", automation_actions)


if __name__ == "__main__":
    unittest.main()
