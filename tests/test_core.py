from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from pixel_pipette.hardware import HardwareController
from pixel_pipette.printing import PrintConfigurationError, PrintEngine
from pixel_pipette.store import Store


class PipetteCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp_dir.name) / "test.sqlite3")
        self.hardware = HardwareController("simulate")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def calibrated_config(self) -> dict:
        return self.store.update_config(
            {
                "paper": {"bottom_right_x": 80.0, "bottom_right_y": 80.0, "deposit_z": 0.0},
                "motion": {"travel_z": 30.0},
                "servo": {
                    "rest_position": 1100,
                    "draw_position": 1060,
                    "purge_position": 1010,
                    "settle_ms": 0,
                },
            }
        )

    def test_simulated_jog_and_origin(self) -> None:
        config = self.store.get_config()
        position = self.hardware.jog(config, "X", 12.5)
        self.assertEqual(position, {"x": 12.5, "y": 0.0, "z": 0.0})
        origin = self.hardware.set_origin(config)
        self.assertEqual(origin, {"x": 0.0, "y": 0.0, "z": 0.0})
        self.assertIn("M17", self.hardware.command_log)

    def test_simulated_printer_button_wait_is_motion_free(self) -> None:
        ready = self.hardware.wait_for_printer_button(
            self.store.get_config(), threading.Event(), "Load paper - press knob"
        )
        self.assertTrue(ready)
        self.assertEqual(self.hardware.command_log, ["M0 Load paper - press knob"])

    def test_print_sequence_maps_cell_centers_and_purges(self) -> None:
        self.calibrated_config()
        self.store.update_color("red", {"intake_z": 6.0, "purge_z": 25.0})
        pixels = ["red"] + [None] * 63
        job = self.store.create_job("Ada", 8, pixels)
        engine = PrintEngine(self.store, self.hardware)

        engine._print_job(job)

        commands = "\n".join(self.hardware.command_log)
        self.assertIn("G1 X5.000 Y5.000", commands)
        self.assertGreaterEqual(commands.count("G1 Z25.000"), 2)
        self.assertIn("SERVO 1010", commands)

        # The first pickup must pre-press directly from rest to draw while the
        # tip is above the well. A purge-before-draw here can aspirate at purge Z.
        first_intake = self.hardware.command_log.index("G1 Z6.000 F300")
        servo_goals_before_intake = [
            command
            for command in self.hardware.command_log[:first_intake]
            if command.startswith("SERVO ")
        ]
        self.assertEqual(servo_goals_before_intake, ["SERVO 1100", "SERVO 1060"])
        self.assertGreater(self.hardware.command_log.index("SERVO 1010"), first_intake)

        # At the paper, dispense normally, lift exactly 1 mm, and only then
        # pulse to purge position before continuing to safe travel height.
        deposit_height = self.hardware.command_log.index("G1 Z0.000 F300")
        purge_height = self.hardware.command_log.index("G1 Z1.000 F300", deposit_height)
        travel_after_deposit = self.hardware.command_log.index(
            "G1 Z30.000 F300", purge_height
        )
        servo_goals_at_deposit = [
            command
            for command in self.hardware.command_log[deposit_height:purge_height]
            if command.startswith("SERVO ")
        ]
        servo_goals_at_purge_height = [
            command
            for command in self.hardware.command_log[purge_height:travel_after_deposit]
            if command.startswith("SERVO ")
        ]
        self.assertEqual(servo_goals_at_deposit, ["SERVO 1060"])
        self.assertEqual(servo_goals_at_purge_height, ["SERVO 1010"])

        self.assertEqual(self.hardware.read_servo_position(self.store.get_config()), 1100)
        updated = next(item for item in self.store.list_jobs() if item["id"] == job["id"])
        self.assertEqual(updated["progress_current"], 1)

    def test_uncalibrated_color_is_rejected_before_motion(self) -> None:
        self.calibrated_config()
        job = self.store.create_job("", 8, ["blue"] + [None] * 63)
        engine = PrintEngine(self.store, self.hardware)

        with self.assertRaises(PrintConfigurationError):
            engine.validate_configuration(job)
        self.assertEqual(self.hardware.command_log, [])


if __name__ == "__main__":
    unittest.main()
