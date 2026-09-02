import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class PackageTests(unittest.TestCase):
    def test_dockerfile_pins_websockets_12_exactly(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertRegex(dockerfile, r"(?m)^RUN .*websockets==12\.0")
        self.assertNotRegex(dockerfile, r"websockets[><~!]=")

    def test_docker_build_runs_runtime_tests_without_package_default_assertions(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("test_main.py", dockerfile)
        self.assertIn("test_server.py", dockerfile)
        self.assertNotIn("test_package.py", dockerfile)
        self.assertNotIn("python -m unittest discover -s tests -v", dockerfile)

    def test_installer_copies_documented_files_and_reloads_the_app_store(self):
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn('"$SOURCE_DIR/install.sh"', installer)
        self.assertIn('"$SOURCE_DIR/uninstall.sh"', installer)
        self.assertIn('"$SOURCE_DIR/ha-timing-script.yaml"', installer)
        self.assertIn('"$SOURCE_DIR/README.md"', installer)
        self.assertIn('"$SOURCE_DIR/CHARGER_CONTROL_WORKAROUNDS.md"', installer)
        self.assertIn('"$SOURCE_DIR/docs"', installer)
        self.assertIn('"$SOURCE_DIR/examples"', installer)
        self.assertIn("ha store reload", installer)
        self.assertNotIn("ha supervisor reload", installer)

        uninstaller = (ROOT / "uninstall.sh").read_text(encoding="utf-8")
        self.assertIn("ha store reload", uninstaller)
        self.assertNotIn("ha supervisor reload", uninstaller)

    def test_community_control_workarounds_are_packaged(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        guide = (ROOT / "CHARGER_CONTROL_WORKAROUNDS.md").read_text(encoding="utf-8")
        self.assertIn("CHARGER_CONTROL_WORKAROUNDS.md", readme)
        self.assertIn('"chargingProfilePurpose": "TxProfile"', guide)
        self.assertIn('"limit": 0', guide)
        self.assertIn("same transaction", guide)
        self.assertIn("above 1 A and at or below 3 A for 15 minutes", guide)
        self.assertIn("Hard Reset", guide)
        self.assertIn("BootNotification", guide)
        self.assertIn("one serialized coordinator", guide)
        self.assertIn("lowest commissioned limit", guide)
        self.assertIn("command-quiescence period", guide)
        self.assertIn("terminal-taper candidate", guide)
        self.assertIn("transaction ID 0", guide)
        self.assertNotIn("192.168.0.", guide)

    def test_generic_everyday_control_examples_are_safe_and_packaged(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        example_guide = (ROOT / "examples" / "README.md").read_text(encoding="utf-8")
        controls = (ROOT / "examples" / "ocular-everyday-controls.yaml").read_text(
            encoding="utf-8"
        )
        dashboard = (ROOT / "examples" / "ocular-dashboard-card.yaml").read_text(
            encoding="utf-8"
        )

        for relative_path in (
            "examples/README.md",
            "examples/ocular-everyday-controls.yaml",
            "examples/ocular-dashboard-card.yaml",
        ):
            self.assertIn(relative_path, readme)
            self.assertTrue((ROOT / relative_path).is_file())

        self.assertIn("input_boolean:", controls)
        self.assertIn("initial: false", controls)
        self.assertIn("ocular_start_charging:", controls)
        self.assertIn("ocular_pause_charging:", controls)
        self.assertIn("ocular_resume_charging:", controls)
        self.assertIn("ocular_stop_charging:", controls)
        self.assertIn("devid: central", controls)
        self.assertNotIn("devid: ocular", controls)
        self.assertIn("chargingProfilePurpose", controls)
        self.assertIn("TxProfile", controls)
        self.assertIn("transactionId", controls)
        self.assertIn("limit", controls)
        self.assertIn("minutes: 15", controls)
        self.assertIn("above 1 A and at or below 3 A", example_guide)
        self.assertIn("6 A", example_guide)
        self.assertIn("32 A", example_guide)
        self.assertIn("script.ocular_start_charging", dashboard)
        self.assertIn("script.ocular_stop_charging", dashboard)

        # These examples are generic charger controls, not this installation's
        # solar/battery policy, and reset is never an everyday action.
        self.assertNotIn("ocpp.reset", controls)
        self.assertNotIn("Hard Reset", controls)
        self.assertNotRegex(controls, r"192\.168\.\d+\.\d+")
        self.assertNotIn("sungrow", controls.lower())
        self.assertNotIn("battery_soc", controls.lower())
        self.assertNotIn("solar_forecast", controls.lower())
        self.assertNotIn("ocular_apply_stable_timing", controls)

    def test_automatic_timing_is_consistent_across_guides(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        guide = (ROOT / "CHARGER_CONTROL_WORKAROUNDS.md").read_text(encoding="utf-8")
        self.assertIn("Automatic post-connect charger timing", readme)
        self.assertIn("automatically applies and verifies", guide)
        self.assertNotIn("rerun the supplied timing script", guide.lower())
        self.assertNotIn("does not generate any of the OCPP commands", guide)

    def test_declares_required_ocpp_service_response_capability(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8").lower()
        self.assertIn("ocpp service-action response capability", readme)
        self.assertIn("reboot_required", readme)
        self.assertIn("must return `value`", readme)
        self.assertIn("custom forks", readme)

    def test_ssh_wording_is_method_agnostic_with_optional_app_examples(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        examples = (ROOT / "examples" / "README.md").read_text(encoding="utf-8")
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        requirements = readme.split("## Requirements", 1)[1].split(
            "Supported Home Assistant host architectures", 1
        )[0]

        self.assertIn("SSH session", readme)
        self.assertIn("SSH session", examples)
        self.assertIn("Terminal & SSH", readme)
        self.assertIn("Advanced SSH & Web Terminal", readme)
        self.assertNotIn("Terminal & SSH", requirements)
        self.assertNotIn("Advanced SSH & Web Terminal", requirements)
        self.assertNotIn("Terminal & SSH", installer)
        self.assertNotIn("Advanced SSH & Web Terminal", installer)

    def test_beginner_diagrams_and_actual_interface_screens_are_linked_and_present(
        self,
    ):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for relative_path in (
            "docs/images/connection-overview.svg",
            "docs/images/ocular-settings-reference.svg",
            "docs/images/ocppsettool-main-official.png",
            "docs/images/ocppsettool-other-settings-official.png",
            "docs/images/ocppsettool-set-ip-official.png",
            "docs/images/ocppsettool-set-mode-official.png",
        ):
            self.assertIn(relative_path, readme)
            self.assertTrue((ROOT / relative_path).is_file())
        self.assertIn("Current charger OCPP server", readme)
        self.assertIn("genuine OCPPSetTool interface captures", readme)
        self.assertIn("Do not copy the charger ID or Exploren server", readme)
        self.assertIn("## Installation prerequisites", readme)
        self.assertNotIn("without being a developer", readme)
        self.assertNotRegex(readme, r"(?m)^bash install\.sh ")
        self.assertGreaterEqual(
            readme.count("bash /config/ocular-ocpp-easy-deploy/install.sh"), 3
        )

    def test_experimental_version_is_bumped_consistently(self):
        config = (ROOT / "config.yaml").read_text(encoding="utf-8")
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        version = re.search(r'(?m)^version: "([^"]+)"$', config).group(1)
        self.assertNotEqual(version, "0.1.3")
        self.assertIn(f'org.opencontainers.image.version="{version}"', dockerfile)
        self.assertIn("stage: experimental", config)

    def test_public_repository_and_optional_support_links_are_consistent(self):
        config = (ROOT / "config.yaml").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        repository_url = "https://github.com/matiebird/ocular-ocpp-compatibility-relay"
        self.assertIn(f'url: "{repository_url}"', config)
        self.assertIn(repository_url, readme)
        self.assertIn("https://ko-fi.com/matiebird", readme)
        self.assertNotIn("buymeacoffee.com", readme.lower())
        self.assertIn("entirely optional", readme.lower())
        self.assertIn("does not affect access", readme.lower())

    def test_generic_high_port_topology_is_packaged(self):
        config = (ROOT / "config.yaml").read_text(encoding="utf-8")
        main = (ROOT / "proxy" / "main.py").read_text(encoding="utf-8")
        self.assertIn("slug: ocular_ocpp_compatibility_relay", config)
        self.assertIn("boot: auto", config)
        self.assertIn("9000/tcp: 19000", config)
        self.assertIn("upstream_port: 9000", config)
        self.assertIn("  - amd64", config)
        self.assertNotIn("  - armv7", config)
        self.assertNotIn("  upstream_base:", config)
        self.assertNotIn('UPSTREAM_BASE = "ws://homeassistant:9001"', main)
        self.assertIn('f"ws://homeassistant:{upstream_port}"', main)
        self.assertIn("charge_point_id: central", config)

    def test_installer_enables_automatic_verified_timing(self):
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        config = (ROOT / "config.yaml").read_text(encoding="utf-8")
        main = (ROOT / "proxy" / "main.py").read_text(encoding="utf-8")
        timing = (ROOT / "ha-timing-script.yaml").read_text(encoding="utf-8")
        guide = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("CHARGER_IP", installer)
        self.assertIn("HOME_ASSISTANT_IP", installer)
        self.assertIn("device_id=config.charge_point_id", main)
        self.assertIn("ha apps install", installer)
        self.assertIn("ha apps start", installer)
        self.assertIn("HeartbeatInterval", timing)
        self.assertIn("ocpp_device_id: central", timing)
        self.assertNotIn("ocular", timing)
        self.assertIn(
            's/^  ocpp_device_id: central$/  ocpp_device_id: $CHARGE_POINT_ID/',
            installer,
        )
        self.assertIn('"$TARGET_DIR/ha-timing-script.yaml"', installer)
        self.assertIn('"$TARGET_DIR/examples/ocular-everyday-controls.yaml"', installer)
        self.assertIn("WebSocketPingInterval", timing)
        self.assertIn("MeterValueSampleInterval", timing)
        self.assertIn("uninstall.sh", guide)
        self.assertIn("automatic post-connect charger timing", guide.lower())
        self.assertIn("connection-critical", guide.lower())
        self.assertIn("telemetry frequency", guide.lower())
        self.assertIn("automatic charger timing", installer.lower())
        self.assertIn("homeassistant_api: true", config)
        self.assertNotIn("required manual post-install step", guide.lower())

    def test_uninstaller_is_included_and_does_not_touch_the_ocpp_backend(self):
        uninstall = (ROOT / "uninstall.sh").read_text(encoding="utf-8")
        self.assertIn("ha apps uninstall", uninstall)
        self.assertNotIn("core.config_entries", uninstall)
        self.assertNotIn("/config/custom_components", uninstall)

    def test_reinstall_backup_is_outside_supervisor_local_app_tree(self):
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn('BACKUP_ROOT="/config/ocular-ocpp-relay-backups"', installer)
        self.assertNotIn('BACKUP="${TARGET_DIR}.backup-', installer)
        self.assertNotIn('BACKUP_DIR="${TARGET_DIR}.backup-', installer)

    def test_install_failure_attempts_automatic_rollback(self):
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn("rollback_on_error", installer)
        self.assertIn("trap rollback_on_error ERR", installer)
        self.assertIn('cp -a "$BACKUP_DIR" "$TARGET_DIR"', installer)
        self.assertIn('ha apps install "$SLUG"', installer)
        self.assertIn('ha apps start "$SLUG"', installer)

    def test_installer_verifies_app_started_state(self):
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn('"state"', installer)
        self.assertIn('"started"', installer)
        self.assertIn('ha apps logs "$SLUG"', installer)


if __name__ == "__main__":
    unittest.main()
