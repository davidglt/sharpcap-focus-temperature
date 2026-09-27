#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Created: 2026-09-27
# Author: David González López-Tercero <davidglt@dragonit.es>
# SPDX-FileCopyrightText: 2026 David González López-Tercero <davidglt@dragonit.es>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for SharpCap autofocus log analysis and focus-state handling."""

import json
import os
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

import sharpcap_focuser


class FocusTemperatureTests(unittest.TestCase):
    def test_missing_configuration_file_is_an_error(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "missing.properties"
            with self.assertRaises(FileNotFoundError):
                sharpcap_focuser.load_tube_focus_config(
                    config_path,
                    "guide",
                )

    def test_missing_tube_section_is_an_error(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "focus.properties"
            config_path.write_text("[main]\nfocus_center = 123\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, r"\[guide\] is missing"):
                sharpcap_focuser.load_tube_focus_config(
                    config_path,
                    "guide",
                )

    def test_required_focus_values_must_be_configured(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "focus.properties"
            config_path.write_text(
                "[main]\nfocus_center = 18706\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ValueError,
                "focus_range, temperature_center",
            ):
                sharpcap_focuser.load_tube_focus_config(
                    config_path,
                    "main",
                )

    def test_configured_focus_values_are_loaded(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "focus.properties"
            config_path.write_text(
                "[guide]\n"
                "focus_center = 391982\n"
                "focus_range = 50000\n"
                "temperature_center = 18.70\n"
                "estimated_tcf = -977.18\n",
                encoding="utf-8",
            )

            config = sharpcap_focuser.load_tube_focus_config(
                config_path,
                "guide",
            )

        self.assertEqual(config["focus_center"], 391982)
        self.assertEqual(config["focus_range"], 50000)
        self.assertEqual(config["temperature_center"], 18.70)
        self.assertEqual(config["estimated_tcf"], -977.18)

    def test_recent_filename_is_included_with_old_modification_time(self):
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory)
            log_file = log_path / (
                f"Log_{date.today().isoformat()}T12_00_00-1.log"
            )
            log_file.write_text("", encoding="utf-8")
            os.utime(log_file, (946684800, 946684800))

            selected = sharpcap_focuser.get_log_files(
                log_path,
                date.today() - timedelta(days=1),
            )

        self.assertIn(log_file, selected)

    def test_old_filename_is_excluded_with_recent_modification_time(self):
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory)
            old_date = date.today() - timedelta(days=10)
            log_file = log_path / f"Log_{old_date.isoformat()}T12_00_00-1.log"
            log_file.write_text("", encoding="utf-8")
            os.utime(log_file, None)

            selected = sharpcap_focuser.get_log_files(
                log_path,
                date.today() - timedelta(days=1),
            )

        self.assertNotIn(log_file, selected)

    def test_nonstandard_filename_uses_modification_date_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory)
            log_file = log_path / "Log_legacy.log"
            log_file.write_text("", encoding="utf-8")
            os.utime(log_file, None)

            selected = sharpcap_focuser.get_log_files(
                log_path,
                date.today() - timedelta(days=1),
            )

        self.assertIn(log_file, selected)

    def test_non_directory_log_path_is_an_error(self):
        with tempfile.TemporaryDirectory() as directory:
            file_path = Path(directory) / "not-a-directory"
            file_path.write_text("", encoding="utf-8")
            with self.assertRaises(NotADirectoryError):
                sharpcap_focuser.get_log_files(file_path, None)

    def test_nonexistent_log_directory_is_an_error(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing-logs"
            with self.assertRaises(FileNotFoundError):
                sharpcap_focuser.get_log_files(missing, None)

    def test_synthetic_generation_is_reproducible_with_seed(self):
        arguments = {
            "focus_center": 391982,
            "temperature_center": 18.70,
            "min_position": 366982,
            "max_position": 416982,
            "tcf": -977.18,
            "samples": 12,
            "degrees_of_freedom": 8,
            "noise_stddev": 500,
            "seed": 20260927,
        }
        first, _ = sharpcap_focuser.generate_synthetic_focus_data(**arguments)
        second, _ = sharpcap_focuser.generate_synthetic_focus_data(**arguments)

        self.assertEqual(
            [(row["TemperatureC"], row["FocuserSteps"]) for row in first],
            [(row["TemperatureC"], row["FocuserSteps"]) for row in second],
        )

    def test_seed_is_available_as_a_command_line_option(self):
        with mock.patch(
            "sys.argv",
            [
                "sharpcap_focuser.py",
                "--generate-synthetic-data",
                "--seed",
                "42",
            ],
        ):
            args = sharpcap_focuser.parse_arguments()

        self.assertEqual(args.seed, 42)

    def test_empty_analysis_marks_existing_state_invalid(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state_path.write_text(
                json.dumps({"focus_ref": 12345, "model_tcf": -60.0}),
                encoding="utf-8",
            )

            written = sharpcap_focuser.write_state_json(
                [],
                state_path,
                None,
                None,
                None,
            )
            state = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertFalse(written)
        self.assertFalse(state["valid"])
        self.assertEqual(state["status"], "invalid")
        self.assertIn("No valid autofocus reference", state["invalid_reason"])
        self.assertEqual(state["focus_ref"], 12345)

    def test_csv_write_failure_keeps_previous_file_intact(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "data.csv"
            output.write_text("previous data\n", encoding="utf-8")

            with mock.patch.object(
                sharpcap_focuser.csv,
                "DictWriter",
                side_effect=OSError("simulated write failure"),
            ):
                with self.assertRaisesRegex(OSError, "simulated write failure"):
                    sharpcap_focuser.write_csv(
                        [{"value": "new"}],
                        output,
                        ["value"],
                    )

            self.assertEqual(output.read_text(encoding="utf-8"), "previous data\n")
            self.assertEqual(list(Path(directory).glob(".data.*.csv")), [])

    def test_chart_is_written_to_destination_and_temp_file_is_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            chart = Path(directory) / "focus.png"
            rows = [
                {"DateTime": "2026-01-01", "TemperatureC": 5.0, "FocuserSteps": 100},
                {"DateTime": "2026-02-01", "TemperatureC": 10.0, "FocuserSteps": 200},
                {"DateTime": "2026-03-01", "TemperatureC": 15.0, "FocuserSteps": 300},
            ]

            sharpcap_focuser.create_chart(
                rows,
                [],
                chart,
                None,
                3.0,
                True,
                100.0,
                300.0,
                -10.0,
                40.0,
                True,
                "Test tube",
                real_count=3,
            )

            self.assertTrue(chart.is_file())
            self.assertEqual(chart.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
            self.assertEqual(list(Path(directory).glob(".focus.*.png")), [])

    def test_valid_and_invalid_state_are_written_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "state.json"
            results = [
                {
                    "DateTime": "2026-09-27 20:00:00",
                    "TemperatureC": 18.7,
                    "FocuserSteps": 391982,
                }
            ]

            self.assertTrue(
                sharpcap_focuser.write_state_json(
                    results,
                    output,
                    -977.18,
                    -0.001023,
                    419.7,
                )
            )
            valid_state = json.loads(output.read_text(encoding="utf-8"))
            self.assertTrue(valid_state["valid"])

            self.assertFalse(
                sharpcap_focuser.write_state_json(
                    [],
                    output,
                    None,
                    None,
                    None,
                )
            )
            invalid_state = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(list(Path(directory).glob(".state.*.json")), [])

        self.assertFalse(invalid_state["valid"])
        self.assertEqual(invalid_state["focus_ref"], 391982)


if __name__ == "__main__":
    unittest.main()
