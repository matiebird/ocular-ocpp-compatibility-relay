from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PackageTests(unittest.TestCase):
    def test_dockerfile_pins_websockets_12_exactly(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertRegex(dockerfile, r"(?m)^RUN .*websockets==12\.0")
        self.assertNotRegex(dockerfile, r"websockets[><~!]=")

    def test_docker_build_includes_files_used_by_package_tests(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        for name in (
            "install.sh",
            "uninstall.sh",
            "ha-timing-script.yaml",
            "README.md",
            "CHARGER_CONTROL_WORKAROUNDS.md",
            "examples",
        ):
            self.assertIn(name, dockerfile)
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn('"$SOURCE_DIR/install.sh"', installer)
        self.assertIn('"$SOURCE_DIR/uninstall.sh"', installer)
        self.assertIn('"$SOURCE_DIR/ha-timing-script.yaml"', installer)
        self.assertIn('"$SOURCE_DIR/README.md"', installer)
        self.assertIn('"$SOURCE_DIR/CHARGER_CONTROL_WORKAROUNDS.md"', installer)
        self.assertIn('"$SOURCE_DIR/docs"', installer)
        self.assertIn('"$SOURCE_DIR/examples"', installer)

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

    def test_ssh_wording_is_method_agnostic(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        examples = (ROOT / "examples" / "README.md").read_text(encoding="utf-8")
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")

        self.assertIn("SSH session", readme)
        self.assertIn("SSH session", examples)
        for text in (readme, examples, installer):
            self.assertNotIn("Advanced SSH & Web Terminal", text)
            self.assertNotIn("Terminal app", text)

    def test_beginner_diagrams_and_actual_interface_screens_are_linked_and_present(self):
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

    def test_one_command_installer_and_timing_script_are_included(self):
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        timing = (ROOT / "ha-timing-script.yaml").read_text(encoding="utf-8")
        guide = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("CHARGER_IP", installer)
        self.assertIn("HOME_ASSISTANT_IP", installer)
        self.assertIn("ha apps install", installer)
        self.assertIn("ha apps start", installer)
        self.assertIn("HeartbeatInterval", timing)
        self.assertIn("WebSocketPingInterval", timing)
        self.assertIn("MeterValueSampleInterval", timing)
        self.assertIn("uninstall.sh", guide)

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
