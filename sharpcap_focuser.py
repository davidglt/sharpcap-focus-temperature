#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 David Gonzalez Lopez-Tercero <davidglt@dragonit.es>
# SPDX-License-Identifier: GPL-3.0-or-later

"""
SharpCap Autofocus Log Extractor and Temperature Regression Plotter.

Extract autofocus results from SharpCap log files, filter them by date and
focuser range, optionally remove outliers using externally studentized
residuals, export the cleaned dataset to CSV, and generate a publication-ready
chart with regression, prediction, legend, and summary tables.

Focus configuration
-------------------
Per-tube focus references and analysis intervals can be stored in the optional
focus_config.properties file beside this script. Copy
focus_config.properties.example to focus_config.properties and set:

    [main]
    focus_center = 18700
    focus_range = 3000
    temperature_center = 15.00

The interval is calculated automatically:

    min_position = focus_center - focus_range / 2
    max_position = focus_center + focus_range / 2

temperature_center is the focuser temperature in degrees Celsius at which
focus_center is valid. It is used by --generate-synthetic-data.

Command-line flags override the local configuration for one execution.

Features
--------
- Parses SharpCap log files and extracts autofocus results.
- Filters results by calendar days and focuser step range.
- Loads optional per-tube focus configuration from a .properties file.
- Supports temporary --focus-center and --focus-range CLI overrides.
- Generates synthetic focus-temperature samples from a TCF.
- Optionally removes outliers using externally studentized residuals.
- Fits a linear regression between temperature and focuser position.
- Predicts focuser position for a target temperature.
- Exports cleaned results and removed outliers to CSV.
- Exports last valid autofocus reference and regression model to JSON.
- Generates a chart with regression and two side summary tables.
- Supports two optical tubes via --tube {main,guide}.
"""

import argparse
import configparser
import csv
import json
import math
import random
import re
from datetime import datetime, timedelta
from pathlib import Path


DEG_C_CHART = "\u00B0C"
DEG_C_CONSOLE = "\u00BAC"
CONFIG_FILENAME = "focus_config.properties"

SYNTHETIC_DEFAULT_SAMPLES = 12
SYNTHETIC_DEFAULT_STUDENT_DOF = 8
SYNTHETIC_DEFAULT_NOISE_STDDEV = 12.0
SYNTHETIC_DEFAULT_TEMPERATURE_AMPLITUDE = 12.0
SYNTHETIC_DEFAULT_TEMPERATURE_NOISE_STDDEV = 1.5
SYNTHETIC_MAX_CLIPPING_FRACTION = 0.20

TUBE_DEFAULTS = {
    "main": {
        "label": "Main tube C8",
        "focus_center": 18700,
        "focus_range": 3000,
        "temperature_center": 15.00,
        "output_csv": "sharpcap_data_focus.csv",
        "output_state": "sharpcap_focus_state.json",
        "chart_name": "sharpcap_focus_temperature.png",
    },
    "guide": {
        "label": "Guide tube 50ED",
        "focus_center": 347000,
        "focus_range": 50000,
        "temperature_center": 15.00,
        "output_csv": "sharpcap_data_focus_guide.csv",
        "output_state": "sharpcap_focus_state_guide.json",
        "chart_name": "sharpcap_focus_temperature_guide.png",
    },
}


def non_negative_float(value: str) -> float:
    """Parse a finite float greater than or equal to zero."""
    try:
        result = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "Value must be a finite number greater than or equal to zero."
        ) from error

    if not math.isfinite(result) or result < 0:
        raise argparse.ArgumentTypeError(
            "Value must be a finite number greater than or equal to zero."
        )
    return result


def positive_int(value: str) -> int:
    """Parse a positive integer."""
    try:
        result = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "Value must be a positive integer."
        ) from error

    if result < 1:
        raise argparse.ArgumentTypeError(
            "Value must be a positive integer."
        )
    return result


def student_dof(value: str) -> int:
    """Parse Student's t degrees of freedom with finite variance."""
    try:
        result = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "Value must be an integer greater than 2."
        ) from error

    if result <= 2:
        raise argparse.ArgumentTypeError(
            "Value must be an integer greater than 2."
        )
    return result


def finite_float(value: str) -> float:
    """Parse a finite float."""
    try:
        result = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "Value must be a finite number."
        ) from error

    if not math.isfinite(result):
        raise argparse.ArgumentTypeError("Value must be a finite number.")
    return result


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Extract SharpCap autofocus results and create CSV and chart."
    )
    parser.add_argument(
        "--tube",
        choices=["main", "guide"],
        default="main",
        help=(
            "Optical tube to analyse. Selects per-tube focus configuration, "
            "output file names, and chart title. Default: main."
        ),
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help=(
            "Path to focus configuration .properties file. Default: "
            "focus_config.properties beside this script."
        ),
    )
    parser.add_argument(
        "--log-path",
        default=str(Path.home() / "AppData" / "Local" / "SharpCap" / "logs"),
        help="SharpCap log folder path.",
    )
    parser.add_argument(
        "--output-csv",
        default=None,
        help="Output CSV file path. Default depends on --tube.",
    )
    parser.add_argument(
        "--output-state-json",
        default=None,
        help=(
            "Output JSON file with last valid autofocus reference and regression "
            "model (TCF, slope, intercept). Default depends on --tube."
        ),
    )
    parser.add_argument(
        "--focus-center",
        type=int,
        default=None,
        help=(
            "Temporarily centre the focuser interval on this position. "
            "Uses the configured interval width unless --focus-range is given."
        ),
    )
    parser.add_argument(
        "--focus-range",
        type=int,
        default=None,
        help=(
            "Temporary total interval width in steps. Requires --focus-center. "
            "Must be a positive even integer."
        ),
    )
    parser.add_argument(
        "--x-min",
        type=finite_float,
        default=None,
        help=(
            "Minimum X-axis limit. Must be used with --x-max. "
            "Overrides calculated range for the chart only."
        ),
    )
    parser.add_argument(
        "--x-max",
        type=finite_float,
        default=None,
        help=(
            "Maximum X-axis limit. Must be used with --x-min. "
            "Overrides calculated range for the chart only."
        ),
    )
    parser.add_argument(
        "--y-min",
        type=finite_float,
        default=-10,
        help="Minimum Y-axis limit. Default: -10.",
    )
    parser.add_argument(
        "--y-max",
        type=finite_float,
        default=40,
        help="Maximum Y-axis limit. Default: 40.",
    )
    parser.add_argument(
        "--auto-axis",
        action="store_true",
        help="Use automatic axis scaling instead of fixed limits.",
    )
    parser.add_argument(
        "--last-days",
        type=int,
        default=None,
        help="Only include events from the last N calendar days, including today.",
    )
    parser.add_argument(
        "--predict-temperature",
        type=finite_float,
        default=None,
        help="Predict focuser position for this temperature in \u00baC.",
    )
    parser.add_argument(
        "--no-remove-outliers",
        action="store_true",
        help=(
            "Disable outlier removal. By default, outliers are removed using "
            "externally studentized residuals."
        ),
    )
    parser.add_argument(
        "--studentized-threshold",
        type=non_negative_float,
        default=3.0,
        help="Absolute studentized residual threshold. Default: 3.0.",
    )
    parser.add_argument(
        "--generate-synthetic-data",
        action="store_true",
        help=(
            "Generate synthetic focus-temperature samples for the selected "
            "tube and exit."
        ),
    )
    parser.add_argument(
        "--tcf",
        type=finite_float,
        default=None,
        help=(
            "Temperature compensation factor in focuser steps per degree "
            "Celsius. Required with --generate-synthetic-data."
        ),
    )
    parser.add_argument(
        "--samples",
        type=positive_int,
        default=SYNTHETIC_DEFAULT_SAMPLES,
        help=(
            "Number of synthetic samples to generate. "
            f"Default: {SYNTHETIC_DEFAULT_SAMPLES}."
        ),
    )
    parser.add_argument(
        "--student-dof",
        type=student_dof,
        default=SYNTHETIC_DEFAULT_STUDENT_DOF,
        help=(
            "Degrees of freedom for the Student's t distribution used to "
            "generate synthetic focuser-position noise. Lower values produce "
            "heavier tails and more samples farther from the expected "
            "focus-temperature relation. Must be an integer greater than 2. "
            f"Default: {SYNTHETIC_DEFAULT_STUDENT_DOF}."
        ),
    )
    parser.add_argument(
        "--noise-stddev",
        type=non_negative_float,
        default=SYNTHETIC_DEFAULT_NOISE_STDDEV,
        help=(
            "Target standard deviation in focuser steps of the random noise "
            "added to synthetic focus positions. The Student's t distribution "
            "is scaled to approximately match this standard deviation. "
            f"Default: {SYNTHETIC_DEFAULT_NOISE_STDDEV:.1f}."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Preview generated synthetic data and diagnostics without writing "
            "the synthetic CSV."
        ),
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help=(
            "Replace an existing synthetic CSV when used with "
            "--generate-synthetic-data."
        ),
    )

    args = parser.parse_args()

    if args.focus_range is not None and args.focus_center is None:
        parser.error("--focus-range requires --focus-center.")

    if (args.x_min is None) != (args.x_max is None):
        parser.error("--x-min and --x-max must be specified together.")

    if args.x_min is not None and args.x_min >= args.x_max:
        parser.error("--x-min must be strictly less than --x-max.")

    if args.y_min >= args.y_max:
        parser.error("--y-min must be strictly less than --y-max.")

    if args.last_days is not None and args.last_days < 1:
        parser.error("--last-days must be 1 or greater.")

    if args.generate_synthetic_data:
        if args.tcf is None:
            parser.error(
                "--tcf is required with --generate-synthetic-data."
            )
    elif args.overwrite:
        parser.error("--overwrite requires --generate-synthetic-data.")

    if args.dry_run and not args.generate_synthetic_data:
        parser.error("--dry-run requires --generate-synthetic-data.")

    return args


def validate_center_and_range(focus_center: int, focus_range: int, source: str):
    """Validate a focus centre/range pair and return the interval."""
    if focus_center < 0:
        raise ValueError(f"{source}: focus_center must not be negative.")
    if focus_range <= 0 or focus_range % 2 != 0:
        raise ValueError(
            f"{source}: focus_range must be a positive even integer."
        )

    half_range = focus_range // 2
    min_position = focus_center - half_range
    max_position = focus_center + half_range

    if min_position < 0:
        raise ValueError(
            f"{source}: calculated minimum position must not be negative."
        )

    return min_position, max_position


def validate_temperature_center(temperature_center: float, source: str):
    """Validate the temperature associated with focus_center."""
    if not math.isfinite(temperature_center):
        raise ValueError(
            f"{source}: temperature_center must be a finite number."
        )


def get_default_config_path() -> Path:
    """Return the default local focus configuration path."""
    return Path(__file__).resolve().with_name(CONFIG_FILENAME)


def load_tube_focus_config(config_path: Path, tube: str, defaults: dict):
    """Load optional per-tube center/range/temperature configuration.

    Missing config files or missing tube sections are non-fatal: built-in
    defaults are retained. Invalid configured numeric values raise ValueError,
    so a typo cannot silently contaminate the regression or synthetic data.
    """
    values = {
        "focus_center": defaults["focus_center"],
        "focus_range": defaults["focus_range"],
        "temperature_center": defaults["temperature_center"],
        "source": "built-in defaults",
    }

    if not config_path.exists():
        return values

    parser = configparser.ConfigParser()
    try:
        with config_path.open("r", encoding="utf-8") as handle:
            parser.read_file(handle)
    except (OSError, configparser.Error) as error:
        raise ValueError(
            f"Could not read configuration file {config_path}: {error}"
        ) from error

    if not parser.has_section(tube):
        return values

    try:
        if parser.has_option(tube, "focus_center"):
            values["focus_center"] = parser.getint(tube, "focus_center")
        if parser.has_option(tube, "focus_range"):
            values["focus_range"] = parser.getint(tube, "focus_range")
        if parser.has_option(tube, "temperature_center"):
            values["temperature_center"] = parser.getfloat(
                tube,
                "temperature_center",
            )
    except ValueError as error:
        raise ValueError(
            f"Invalid numeric focus configuration in [{tube}] "
            f"of {config_path}: {error}"
        ) from error

    source = f"Configuration [{tube}] in {config_path}"
    validate_center_and_range(
        values["focus_center"],
        values["focus_range"],
        source,
    )
    validate_temperature_center(values["temperature_center"], source)
    values["source"] = str(config_path)
    return values


def resolve_focus_interval(args, config_values: dict):
    """Resolve focus filtering and chart interval.

    Precedence:
      1. Temporary --focus-center plus optional --focus-range.
      2. Per-tube focus_config.properties values.
      3. Built-in TUBE_DEFAULTS values.
    """
    if args.focus_center is not None:
        focus_center = args.focus_center
        focus_range = (
            config_values["focus_range"]
            if args.focus_range is None
            else args.focus_range
        )
        interval_source = "command-line focus center/range override"
    else:
        focus_center = config_values["focus_center"]
        focus_range = config_values["focus_range"]
        interval_source = config_values["source"]

    min_position, max_position = validate_center_and_range(
        focus_center,
        focus_range,
        interval_source,
    )

    if args.x_min is not None:
        x_min = args.x_min
        x_max = args.x_max
    else:
        x_min = float(min_position)
        x_max = float(max_position)

    return {
        "focus_center": focus_center,
        "focus_range": focus_range,
        "min_position": min_position,
        "max_position": max_position,
        "x_min": x_min,
        "x_max": x_max,
        "source": interval_source,
    }


def get_start_date(last_days: int | None):
    """Return the first included calendar date or None for all history."""
    if last_days is None:
        return None
    today = datetime.now().date()
    return today - timedelta(days=last_days - 1)


def get_log_files(log_path: Path, start_date):
    """Return SharpCap log files passing the optional date filter."""
    log_files = sorted(log_path.glob("Log_*.log"))
    if start_date is None:
        return log_files

    result = []
    for file in log_files:
        if datetime.fromtimestamp(file.stat().st_mtime).date() < start_date:
            continue
        file_date_str = extract_date_from_filename(file)
        try:
            file_date = datetime.strptime(file_date_str, "%Y-%m-%d").date()
        except ValueError:
            file_date = datetime.fromtimestamp(file.stat().st_mtime).date()
        if file_date >= start_date:
            result.append(file)
    return result


def extract_date_from_filename(file_path: Path) -> str:
    """Extract YYYY-MM-DD from a SharpCap log filename."""
    match = re.search(r"Log_(\d{4}-\d{2}-\d{2})T", file_path.name)
    if match:
        return match.group(1)
    return datetime.fromtimestamp(file_path.stat().st_mtime).strftime(
        "%Y-%m-%d"
    )


def parse_logs(log_files, min_position: int, max_position: int, start_date):
    """Extract valid autofocus events from SharpCap logs."""
    time_regex = re.compile(
        r"^(?:Info|Debug|Warning|Error)\s+"
        r"(?P<time>\d{1,2}:\d{2}:\d{2})(?:\.\d+)?"
    )
    autofocus_regex = re.compile(
        r"Autofocus result\s*:\s*best focus at\s+"
        r"(?P<position>-?\d+(?:[.,]\d+)?)\s+"
        r"with focuser temperature of\s+"
        r"(?P<temperature>-?\d+(?:[.,]\d+)?)\s*C",
        re.IGNORECASE,
    )

    results = []
    for log_file in log_files:
        log_date = extract_date_from_filename(log_file)
        with log_file.open("r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                time_match = time_regex.match(line)
                if not time_match:
                    continue

                autofocus_match = autofocus_regex.search(line)
                if not autofocus_match:
                    continue

                event_dt = datetime.strptime(
                    f"{log_date} {time_match.group('time')}",
                    "%Y-%m-%d %H:%M:%S",
                )
                if start_date is not None and event_dt.date() < start_date:
                    continue

                position = float(
                    autofocus_match.group("position").replace(",", ".")
                )
                if position < min_position or position > max_position:
                    continue

                temperature = float(
                    autofocus_match.group("temperature").replace(",", ".")
                )
                results.append(
                    {
                        "DateTime": event_dt.strftime("%Y-%m-%d %H:%M:%S"),
                        "TemperatureC": round(temperature, 2),
                        "FocuserSteps": round(position),
                    }
                )

    results.sort(key=lambda item: item["DateTime"])
    return results


def derive_synthetic_csv_path(output_csv: Path) -> Path:
    """Return the synthetic CSV path associated with an output CSV."""
    name = output_csv.name.replace(
        "sharpcap_data_focus",
        "sharpcap_synthetic_data_focus",
    )
    return output_csv.with_name(name)


def sample_student_t(degrees_of_freedom: int) -> float:
    """Return a Student's t sample without requiring scipy or numpy."""
    normal_sample = random.gauss(0.0, 1.0)
    chi_square = sum(
        random.gauss(0.0, 1.0) ** 2
        for _ in range(degrees_of_freedom)
    )
    return normal_sample / math.sqrt(chi_square / degrees_of_freedom)


def calculate_synthetic_temperature(
    fraction: float,
    temperature_center: float,
) -> float:
    """Return a seasonal temperature with summer maximum and variation."""
    seasonal = (
        SYNTHETIC_DEFAULT_TEMPERATURE_AMPLITUDE
        * math.cos(2.0 * math.pi * (fraction - 0.50))
    )
    random_variation = random.gauss(
        0.0,
        SYNTHETIC_DEFAULT_TEMPERATURE_NOISE_STDDEV,
    )
    return temperature_center + seasonal + random_variation


def generate_synthetic_focus_data(
    focus_center: int,
    temperature_center: float,
    min_position: int,
    max_position: int,
    tcf: float,
    samples: int,
    degrees_of_freedom: int,
    noise_stddev: float,
) -> tuple[list, int]:
    """Generate synthetic focus-temperature samples.

    Samples are evenly distributed over the previous 365 days. Temperatures
    follow a smooth annual pattern with a summer maximum. Position residuals
    use a Student's t distribution normalized to the target standard deviation.

    Returns generated rows and the count of focus positions clipped to the
    selected focus interval.
    """
    if samples < 1:
        raise ValueError("Synthetic sample count must be at least 1.")
    if degrees_of_freedom <= 2:
        raise ValueError(
            "Student's t degrees of freedom must be greater than 2."
        )
    if not math.isfinite(tcf):
        raise ValueError("TCF must be a finite number.")
    if not math.isfinite(noise_stddev) or noise_stddev < 0:
        raise ValueError(
            "Synthetic noise standard deviation must be finite and non-negative."
        )

    variance_normalizer = math.sqrt(
        (degrees_of_freedom - 2) / degrees_of_freedom
    )
    end_time = datetime.now().replace(microsecond=0)
    start_time = end_time - timedelta(days=365)
    time_span = end_time - start_time
    rows = []
    clipped_count = 0

    for index in range(samples):
        fraction = 0.5 if samples == 1 else index / (samples - 1)
        event_dt = start_time + time_span * fraction
        temperature = calculate_synthetic_temperature(
            fraction,
            temperature_center,
        )
        noise = (
            noise_stddev
            * variance_normalizer
            * sample_student_t(degrees_of_freedom)
        )
        expected_position = (
            focus_center + tcf * (temperature - temperature_center)
        )
        unconstrained_position = round(expected_position + noise)
        position = min(
            max(unconstrained_position, min_position),
            max_position,
        )
        if position != unconstrained_position:
            clipped_count += 1

        rows.append(
            {
                "DateTime": event_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "TemperatureC": round(temperature, 2),
                "FocuserSteps": position,
            }
        )

    return rows, clipped_count


def calculate_tcf_from_rows(rows: list):
    """Fit T = k*s + b and return TCF, slope, intercept and correlation."""
    import numpy as np

    if len(rows) < 2:
        return None, None, None, None

    positions = np.array(
        [row["FocuserSteps"] for row in rows],
        dtype=float,
    )
    temperatures = np.array(
        [row["TemperatureC"] for row in rows],
        dtype=float,
    )

    if len(np.unique(positions)) < 2:
        return None, None, None, None

    slope, intercept = np.polyfit(positions, temperatures, 1)
    if math.isclose(slope, 0.0, abs_tol=1e-12):
        recovered_tcf = None
    else:
        recovered_tcf = 1.0 / slope

    if len(np.unique(temperatures)) < 2:
        correlation = None
    else:
        correlation = float(np.corrcoef(positions, temperatures)[0, 1])

    return recovered_tcf, slope, intercept, correlation


def print_synthetic_diagnostics(
    tube_label: str,
    interval: dict,
    temperature_center: float,
    synthetic_csv: Path,
    tcf: float,
    samples: int,
    degrees_of_freedom: int,
    noise_stddev: float,
    rows: list,
    clipped_count: int,
    recovered_tcf,
    correlation,
    dry_run: bool,
):
    """Print synthetic generation summary and regression diagnostics."""
    positions = [row["FocuserSteps"] for row in rows]
    temperatures = [row["TemperatureC"] for row in rows]

    print("Synthetic focus data:")
    print(f"Tube: {tube_label}")
    print(f"Focus configuration source: {interval['source']}")
    print(
        f"Focus center: {interval['focus_center']} steps at "
        f"{temperature_center:.2f} {DEG_C_CONSOLE}"
    )
    print(
        f"Configured focus interval: {interval['min_position']} to "
        f"{interval['max_position']} steps"
    )
    print(f"Samples: {samples}")
    print(f"TCF input: {tcf:.2f} steps/{DEG_C_CONSOLE}")
    print(f"Student's t degrees of freedom: {degrees_of_freedom}")
    print(f"Noise target standard deviation: {noise_stddev:.1f} steps")
    print(
        f"Generated temperature range: {min(temperatures):.2f} to "
        f"{max(temperatures):.2f} {DEG_C_CONSOLE}"
    )
    print(
        f"Generated focus range: {min(positions)} to "
        f"{max(positions)} steps"
    )
    print(f"Positions clipped to interval: {clipped_count}")

    if recovered_tcf is None:
        print("Recovered TCF: unavailable.")
    else:
        difference = recovered_tcf - tcf
        print(f"Recovered TCF: {recovered_tcf:.2f} steps/{DEG_C_CONSOLE}")
        print(f"TCF difference: {difference:+.2f} steps/{DEG_C_CONSOLE}")

    if correlation is not None:
        print(f"Position-temperature correlation: {correlation:.4f}")

    clipping_fraction = clipped_count / samples
    if clipping_fraction > 0:
        print(
            "Warning: generated positions were clipped to the configured "
            "focus interval."
        )
    if clipping_fraction > SYNTHETIC_MAX_CLIPPING_FRACTION:
        print(
            "Warning: more than "
            f"{SYNTHETIC_MAX_CLIPPING_FRACTION:.0%} of samples were clipped. "
            "Consider widening focus_range, reducing the temperature span, "
            "or using a smaller absolute TCF."
        )

    print(f"Synthetic CSV: {synthetic_csv}")
    if dry_run:
        print("Dry run: no synthetic CSV was written.")


def load_synthetic_csv(
    output_csv: Path,
    min_position: int,
    max_position: int,
) -> list:
    """Load valid synthetic points from the per-tube synthetic CSV."""
    path = derive_synthetic_csv_path(output_csv)
    if not path.exists():
        return []

    rows = []
    skipped = 0
    with path.open("r", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            steps = round(float(row["FocuserSteps"]))
            if steps < min_position or steps > max_position:
                skipped += 1
                continue
            rows.append(
                {
                    "DateTime": row["DateTime"].strip(),
                    "TemperatureC": round(float(row["TemperatureC"]), 2),
                    "FocuserSteps": steps,
                    "_synthetic": True,
                }
            )

    message = f"Synthetic CSV found: {path} ({len(rows)} points"
    if skipped:
        message += (
            f", {skipped} skipped — outside "
            f"[{min_position}, {max_position}]"
        )
    print(f"{message})")
    return rows


def filter_outliers_studentized(results, threshold: float):
    """Remove points whose externally studentized residual exceeds threshold."""
    import numpy as np
    import statsmodels.api as sm

    if len(results) < 5:
        return results, [], 0

    x_values = np.array(
        [row["FocuserSteps"] for row in results],
        dtype=float,
    )
    y_values = np.array(
        [row["TemperatureC"] for row in results],
        dtype=float,
    )
    if len(np.unique(x_values)) < 2:
        return results, [], 0

    design_matrix = sm.add_constant(x_values)
    model = sm.OLS(y_values, design_matrix).fit()
    studentized = model.get_influence().resid_studentized_external

    filtered = []
    removed = []
    for row, residual in zip(results, studentized):
        diagnostic = row.copy()
        diagnostic["StudentizedResidual"] = round(float(residual), 3)
        diagnostic["Synthetic"] = "yes" if row.get("_synthetic") else "no"

        if abs(residual) > threshold:
            diagnostic["Reason"] = (
                f"Studentized residual > {threshold:.1f}"
            )
            removed.append(diagnostic)
        else:
            filtered.append(row)

    return filtered, removed, len(removed)


def write_csv(results, output_csv: Path, fieldnames):
    """Write result rows to a CSV file."""
    with output_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fieldnames,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(results)


def write_state_json(results, output_json: Path, inverse_slope, slope, intercept):
    """Write the latest real autofocus result and fitted model to JSON."""
    real_results = [row for row in results if not row.get("_synthetic")]
    if not real_results:
        return False

    last_result = real_results[-1]
    state = {
        "timestamp_ref": last_result["DateTime"],
        "temp_ref": round(float(last_result["TemperatureC"]), 2),
        "focus_ref": int(last_result["FocuserSteps"]),
        "last_temp_applied": round(float(last_result["TemperatureC"]), 2),
        "last_focus_applied": int(last_result["FocuserSteps"]),
        "model_tcf": (
            None if inverse_slope is None else round(float(inverse_slope), 2)
        ),
        "model_inv_tcf": (
            None if slope is None else round(float(slope), 6)
        ),
        "model_intercept_c": (
            None if intercept is None else round(float(intercept), 3)
        ),
    }
    with output_json.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    return True


def style_table(table, fontsize=7.0, header_height=0.058, row_height=0.050):
    """Apply the common visual style to a Matplotlib table."""
    table.auto_set_font_size(False)
    table.set_fontsize(fontsize)
    for (row, _column), cell in table.get_celld().items():
        cell.set_edgecolor("#cccccc")
        cell.set_linewidth(0.8)
        if row == 0:
            cell.set_text_props(weight="bold")
            cell.set_facecolor("#f0f0f0")
            cell.set_height(header_height)
        else:
            cell.set_facecolor("white")
            cell.set_height(row_height)


def create_chart(
    results,
    removed_outliers,
    chart_path: Path,
    predict_temperature: float | None,
    studentized_threshold: float,
    outlier_mode_enabled: bool,
    x_min: float,
    x_max: float,
    y_min: float,
    y_max: float,
    auto_axis: bool,
    tube_label: str,
    real_count: int,
):
    """Create the regression chart and return its fitted model values."""
    import matplotlib

    matplotlib.use("Agg")

    import matplotlib.pyplot as plt
    import numpy as np

    dc = DEG_C_CHART
    real_results = [row for row in results if not row.get("_synthetic")]
    synth_results = [row for row in results if row.get("_synthetic")]
    synthetic_count = len(synth_results)

    x_all = np.array(
        [row["FocuserSteps"] for row in results],
        dtype=float,
    )
    y_all = np.array(
        [row["TemperatureC"] for row in results],
        dtype=float,
    )
    x_real = np.array(
        [row["FocuserSteps"] for row in real_results],
        dtype=float,
    )
    y_real = np.array(
        [row["TemperatureC"] for row in real_results],
        dtype=float,
    )

    fig = plt.figure(figsize=(11.2, 6.4))
    ax = fig.add_axes([0.08, 0.16, 0.58, 0.74])
    info_ax = fig.add_axes([0.70, 0.12, 0.28, 0.78])
    info_ax.axis("off")

    scatter = ax.scatter(
        x_real,
        y_real,
        color="navy",
        s=38,
        label="Autofocus results",
        zorder=3,
    )

    synth_scatter = None
    if synth_results:
        x_synth = np.array(
            [row["FocuserSteps"] for row in synth_results],
            dtype=float,
        )
        y_synth = np.array(
            [row["TemperatureC"] for row in synth_results],
            dtype=float,
        )
        synth_scatter = ax.scatter(
            x_synth,
            y_synth,
            color="green",
            s=38,
            marker="s",
            label="Synthetic data",
            zorder=3,
        )

    outlier_scatter = None
    outlier_x = None
    if removed_outliers:
        outlier_x = np.array(
            [row["FocuserSteps"] for row in removed_outliers],
            dtype=float,
        )
        outlier_y = np.array(
            [row["TemperatureC"] for row in removed_outliers],
            dtype=float,
        )
        outlier_scatter = ax.scatter(
            outlier_x,
            outlier_y,
            s=72,
            facecolors="none",
            edgecolors="darkorange",
            linewidths=1.5,
            label="Removed outliers",
            zorder=4,
        )

    slope = None
    intercept = None
    inverse_slope = None
    predicted_steps_rounded = None
    solid_line = None
    dashed_proxy = None
    prediction_marker = None

    data_x_min = float(x_all.min()) if len(x_all) > 0 else 0.0
    data_x_max = float(x_all.max()) if len(x_all) > 0 else 0.0

    if len(results) >= 2 and len(np.unique(x_all)) >= 2:
        slope, intercept = np.polyfit(x_all, y_all, 1)
        if not math.isclose(slope, 0.0, abs_tol=1e-12):
            inverse_slope = 1.0 / slope

        if auto_axis:
            axis_x_min = data_x_min
            axis_x_max = data_x_max
            if outlier_x is not None and len(outlier_x) > 0:
                axis_x_min = min(axis_x_min, float(outlier_x.min()))
                axis_x_max = max(axis_x_max, float(outlier_x.max()))
        else:
            axis_x_min = float(x_min)
            axis_x_max = float(x_max)

        solid_x = np.linspace(data_x_min, data_x_max, 200)
        solid_y = slope * solid_x + intercept
        solid_line, = ax.plot(
            solid_x,
            solid_y,
            color="red",
            linewidth=1.9,
            linestyle="-",
            zorder=2,
        )

        if axis_x_min < data_x_min:
            left_x = np.linspace(axis_x_min, data_x_min, 80)
            ax.plot(
                left_x,
                slope * left_x + intercept,
                color="red",
                linewidth=1.6,
                linestyle="--",
                zorder=1,
            )

        if axis_x_max > data_x_max:
            right_x = np.linspace(data_x_max, axis_x_max, 80)
            ax.plot(
                right_x,
                slope * right_x + intercept,
                color="red",
                linewidth=1.6,
                linestyle="--",
                zorder=1,
            )

        dashed_proxy, = ax.plot(
            [],
            [],
            color="red",
            linewidth=1.6,
            linestyle="--",
        )

        if (
            predict_temperature is not None
            and not math.isclose(slope, 0.0, abs_tol=1e-12)
        ):
            predicted_steps = (predict_temperature - intercept) / slope
            predicted_steps_rounded = round(predicted_steps)
            prediction_marker = ax.scatter(
                [predicted_steps],
                [predict_temperature],
                color="green",
                s=70,
                marker="D",
                zorder=5,
            )
            ax.annotate(
                f"{predicted_steps_rounded} steps @ "
                f"{predict_temperature:.2f} {dc}",
                xy=(predicted_steps, predict_temperature),
                xytext=(10, 10),
                textcoords="offset points",
                fontsize=8,
                color="darkgreen",
                bbox=dict(boxstyle="round", facecolor="white", alpha=0.85),
                arrowprops=dict(arrowstyle="->", color="darkgreen"),
            )

    ax.set_title(
        f"Focuser Position vs Temperature — {tube_label}",
        fontsize=11,
    )
    ax.set_xlabel("s: Focuser Steps", fontsize=9.5, labelpad=6)
    ax.set_ylabel(f"T: Temperature ({dc})", fontsize=9.5)
    ax.tick_params(axis="both", labelsize=8.5)
    ax.grid(True, alpha=0.3)

    if auto_axis:
        ax.autoscale(enable=True, axis="both", tight=False)
        ax.margins(x=0.05, y=0.08)
    else:
        ax.set_xlim(x_min, x_max)
        ax.set_ylim(y_min, y_max)

    handles = [scatter]
    labels = [f"Autofocus results ({real_count} pts)"]
    if synth_scatter is not None:
        handles.append(synth_scatter)
        labels.append(f"Synthetic data ({synthetic_count} pts)")
    if outlier_scatter is not None:
        handles.append(outlier_scatter)
        labels.append(f"Removed outliers ({len(removed_outliers)} pts)")
    if solid_line is not None:
        handles.append(solid_line)
        labels.append("Regression line (measured range)")
    if dashed_proxy is not None:
        handles.append(dashed_proxy)
        labels.append("Regression line (estimated range)")
    if prediction_marker is not None:
        handles.append(prediction_marker)
        labels.append(f"Prediction at {predict_temperature:.2f} {dc}")

    legend = info_ax.legend(
        handles,
        labels,
        loc="upper left",
        frameon=True,
        borderpad=0.5,
        labelspacing=0.5,
        fontsize=8,
    )

    first_focus = None
    last_focus = None
    focus_span_steps = None
    if real_results:
        first_focus = real_results[0]["FocuserSteps"]
        last_focus = real_results[-1]["FocuserSteps"]
        focus_span_steps = last_focus - first_focus

    regression_equation = "-"
    if slope is not None and intercept is not None:
        regression_equation = "T = k*s + b"

    model_rows = [
        ["Regression equation", regression_equation],
        ["T", f"Temperature ({dc})"],
        ["s", "Focuser Steps"],
    ]
    if predict_temperature is not None:
        model_rows.append(["Target T", f"{predict_temperature:.2f} {dc}"])
    model_rows.append(
        [f"k ({dc}/step)", f"{slope:.6f}" if slope is not None else "-"]
    )
    model_rows.append(
        [
            f"TCF = 1/k (step/{dc})",
            f"{inverse_slope:.2f}" if inverse_slope is not None else "-",
        ]
    )
    model_rows.append(
        [f"b ({dc})", f"{intercept:.3f}" if intercept is not None else "-"]
    )
    if predicted_steps_rounded is not None:
        model_rows.append(["Focus(T)", f"{predicted_steps_rounded} steps"])

    focus_rows = []
    if first_focus is not None:
        focus_rows.append(["First focus", f"{first_focus} steps"])
    if last_focus is not None:
        focus_rows.append(["Last focus", f"{last_focus} steps"])
    if focus_span_steps is not None:
        focus_rows.append(["Delta focus", f"{focus_span_steps:+d} steps"])
    focus_rows.append(["Autofocus pts", str(real_count)])
    if synthetic_count > 0:
        focus_rows.append(["Synthetic pts", str(synthetic_count)])
    if outlier_mode_enabled:
        focus_rows.append(["Outliers", f"{len(removed_outliers)} removed"])
        focus_rows.append(["Threshold", f"|t| > {studentized_threshold:.1f}"])
    else:
        focus_rows.append(["Outliers", "Disabled"])
    if auto_axis:
        focus_rows.append(["X axis", "Auto (steps)"])
        focus_rows.append(["Y axis", f"Auto ({dc})"])
    else:
        focus_rows.append(["X axis", f"{x_min:.0f} to {x_max:.0f} steps"])
        focus_rows.append(["Y axis", f"{y_min:.0f} to {y_max:.0f} {dc}"])

    info_ax.text(
        0.01,
        0.690,
        "Model",
        fontsize=9,
        fontweight="bold",
        ha="left",
        va="bottom",
    )
    info_ax.text(
        0.01,
        0.365,
        "Focus",
        fontsize=9,
        fontweight="bold",
        ha="left",
        va="bottom",
    )

    model_table = info_ax.table(
        cellText=model_rows,
        colLabels=["Item", "Value"],
        colLoc="left",
        cellLoc="left",
        colWidths=[0.41, 0.55],
        bbox=[0.01, 0.465, 0.96, 0.22],
    )
    style_table(model_table)

    focus_table = info_ax.table(
        cellText=focus_rows,
        colLabels=["Item", "Value"],
        colLoc="left",
        cellLoc="left",
        colWidths=[0.42, 0.54],
        bbox=[0.01, 0.11, 0.96, 0.25],
    )
    style_table(focus_table)

    fig.savefig(
        chart_path,
        dpi=130,
        bbox_inches="tight",
        bbox_extra_artists=(legend,),
    )
    plt.close(fig)
    return slope, intercept, inverse_slope, predicted_steps_rounded


def run_synthetic_generation(
    args,
    tube_label: str,
    interval: dict,
    temperature_center: float,
    output_csv: Path,
):
    """Generate or preview the per-tube synthetic data CSV."""
    synthetic_csv = derive_synthetic_csv_path(output_csv)

    if synthetic_csv.exists() and not args.overwrite and not args.dry_run:
        raise ValueError(
            f"Synthetic CSV already exists: {synthetic_csv}. "
            "Use --overwrite to replace it or --dry-run to preview new data."
        )

    rows, clipped_count = generate_synthetic_focus_data(
        focus_center=interval["focus_center"],
        temperature_center=temperature_center,
        min_position=interval["min_position"],
        max_position=interval["max_position"],
        tcf=args.tcf,
        samples=args.samples,
        degrees_of_freedom=args.student_dof,
        noise_stddev=args.noise_stddev,
    )
    recovered_tcf, _slope, _intercept, correlation = calculate_tcf_from_rows(
        rows
    )

    print_synthetic_diagnostics(
        tube_label=tube_label,
        interval=interval,
        temperature_center=temperature_center,
        synthetic_csv=synthetic_csv,
        tcf=args.tcf,
        samples=args.samples,
        degrees_of_freedom=args.student_dof,
        noise_stddev=args.noise_stddev,
        rows=rows,
        clipped_count=clipped_count,
        recovered_tcf=recovered_tcf,
        correlation=correlation,
        dry_run=args.dry_run,
    )

    if args.dry_run:
        return

    write_csv(
        rows,
        synthetic_csv,
        ["DateTime", "TemperatureC", "FocuserSteps"],
    )
    print("Synthetic CSV written successfully.")


def main():
    args = parse_arguments()

    tube_defaults = TUBE_DEFAULTS[args.tube]
    config_path = (
        args.config.resolve()
        if args.config is not None
        else get_default_config_path()
    )
    config_values = load_tube_focus_config(
        config_path,
        args.tube,
        tube_defaults,
    )
    interval = resolve_focus_interval(args, config_values)

    tube_label = tube_defaults["label"]
    min_position = interval["min_position"]
    max_position = interval["max_position"]
    x_min = interval["x_min"]
    x_max = interval["x_max"]
    temperature_center = config_values["temperature_center"]

    output_csv_name = (
        args.output_csv
        if args.output_csv is not None
        else tube_defaults["output_csv"]
    )
    output_state_name = (
        args.output_state_json
        if args.output_state_json is not None
        else tube_defaults["output_state"]
    )

    output_csv = Path(output_csv_name).resolve()

    if args.generate_synthetic_data:
        run_synthetic_generation(
            args=args,
            tube_label=tube_label,
            interval=interval,
            temperature_center=temperature_center,
            output_csv=output_csv,
        )
        return

    remove_outliers = not args.no_remove_outliers
    start_date = get_start_date(args.last_days)

    log_path = Path(args.log_path)
    outliers_csv = output_csv.with_name(
        output_csv.stem.replace(
            "sharpcap_data_focus",
            "sharpcap_removed_outliers",
        )
        + output_csv.suffix
    )
    chart_path = output_csv.with_name(tube_defaults["chart_name"])
    state_json = Path(output_state_name).resolve()

    print(f"Tube: {tube_label}")
    print(f"Focus configuration source: {interval['source']}")
    print(f"Focus center: {interval['focus_center']} steps")
    print(f"Focus range: {interval['focus_range']} steps")
    print(
        f"Position filter: {min_position} to {max_position} steps"
    )

    log_files = get_log_files(log_path, start_date)
    real_results = parse_logs(
        log_files,
        min_position,
        max_position,
        start_date,
    )
    synthetic_rows = load_synthetic_csv(
        output_csv,
        min_position,
        max_position,
    )

    combined = real_results + synthetic_rows
    combined.sort(key=lambda item: item["DateTime"])

    original_count = len(real_results)
    removed_outliers = []
    removed_count = 0

    if remove_outliers and combined:
        combined, removed_outliers, removed_count = filter_outliers_studentized(
            combined,
            args.studentized_threshold,
        )

    real_clean = [row for row in combined if not row.get("_synthetic")]

    write_csv(
        real_clean,
        output_csv,
        ["DateTime", "TemperatureC", "FocuserSteps"],
    )
    print(f"CSV created: {output_csv}")

    if remove_outliers:
        write_csv(
            removed_outliers,
            outliers_csv,
            [
                "DateTime",
                "TemperatureC",
                "FocuserSteps",
                "StudentizedResidual",
                "Synthetic",
                "Reason",
            ],
        )
        print(f"Outliers CSV created: {outliers_csv}")
        print(f"Outliers written: {len(removed_outliers)}")

    synthetic_count = len(
        [row for row in combined if row.get("_synthetic")]
    )

    print(f"Extracted autofocus results: {original_count}")
    print(
        f"Remaining points after filters: {len(real_clean)} real + "
        f"{synthetic_count} synthetic"
    )

    if start_date is not None:
        print(f"Calendar-day filter start: {start_date.isoformat()}")

    if remove_outliers:
        print(f"Outliers removed: {removed_count}")
        print(
            "Outlier method: externally studentized residuals "
            f"(|t| > {args.studentized_threshold:.1f})"
        )
    else:
        print("Outlier removal: disabled")

    if args.auto_axis:
        print("Axis mode: automatic")
    else:
        print(f"X axis limits: {x_min} to {x_max} steps")
        print(
            f"Y axis limits: {args.y_min} to {args.y_max} "
            f"{DEG_C_CONSOLE}"
        )

    if real_clean:
        first_focus = real_clean[0]["FocuserSteps"]
        last_focus = real_clean[-1]["FocuserSteps"]
        focus_span_steps = last_focus - first_focus
        print(f"First focus: {first_focus} steps")
        print(f"Last focus: {last_focus} steps")
        print(f"Delta focus: {focus_span_steps:+d} steps")

        slope, intercept, inverse_slope, predicted_steps_rounded = create_chart(
            combined,
            removed_outliers,
            chart_path,
            args.predict_temperature,
            args.studentized_threshold,
            remove_outliers,
            x_min,
            x_max,
            args.y_min,
            args.y_max,
            args.auto_axis,
            tube_label,
            real_count=len(real_clean),
        )

        if slope is not None and intercept is not None:
            print(
                f"Regression equation: T = {slope:.6f} * Steps + "
                f"{intercept:.3f}"
            )
            print(f"k = {slope:.6f} {DEG_C_CONSOLE}/step")
            if inverse_slope is not None:
                print(
                    f"TCF = 1/k = {inverse_slope:.2f} "
                    f"steps/{DEG_C_CONSOLE}"
                )
            print(f"b = {intercept:.3f} {DEG_C_CONSOLE}")
            if (
                predicted_steps_rounded is not None
                and args.predict_temperature is not None
            ):
                print(
                    f"Predicted focus for "
                    f"{args.predict_temperature:.2f} {DEG_C_CONSOLE}: "
                    f"{predicted_steps_rounded} steps"
                )
        else:
            print("Regression could not be calculated with the available points.")

        state_written = write_state_json(
            combined,
            state_json,
            inverse_slope,
            slope,
            intercept,
        )
        if state_written:
            print(f"State JSON created: {state_json}")
            print(
                f"Reference autofocus timestamp: "
                f"{real_clean[-1]['DateTime']}"
            )
            print(
                f"Reference temperature: "
                f"{real_clean[-1]['TemperatureC']:.2f} "
                f"{DEG_C_CONSOLE}"
            )
            print(
                f"Reference focus: "
                f"{real_clean[-1]['FocuserSteps']} steps"
            )
        else:
            print(
                "State JSON was not created: no valid autofocus reference "
                "available."
            )

        print(f"Chart created: {chart_path}")
    else:
        print("Chart was not created: no real autofocus results were found.")


if __name__ == "__main__":
    main()