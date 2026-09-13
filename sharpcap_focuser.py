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

The interval is calculated automatically:

    min_position = focus_center - focus_range / 2
    max_position = focus_center + focus_range / 2

Command-line flags override the local configuration for one execution.

Features
--------
- Parses SharpCap log files and extracts autofocus results.
- Filters results by calendar days and focuser step range.
- Loads optional per-tube focus configuration from a .properties file.
- Supports temporary --focus-center and --focus-range CLI overrides.
- Optionally removes outliers using externally studentized residuals.
- Fits a linear regression between focuser position and temperature.
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
import re
from datetime import datetime, timedelta
from pathlib import Path


DEG_C_CHART = "\u00B0C"
DEG_C_CONSOLE = "\u00BAC"
CONFIG_FILENAME = "focus_config.properties"

TUBE_DEFAULTS = {
    "main": {
        "label": "Main tube C8",
        "focus_center": 18700,
        "focus_range": 3000,
        "output_csv": "sharpcap_data_focus.csv",
        "output_state": "sharpcap_focus_state.json",
        "chart_name": "sharpcap_focus_temperature.png",
    },
    "guide": {
        "label": "Guide tube 50ED",
        "focus_center": 347000,
        "focus_range": 50000,
        "output_csv": "sharpcap_data_focus_guide.csv",
        "output_state": "sharpcap_focus_state_guide.json",
        "chart_name": "sharpcap_focus_temperature_guide.png",
    },
}


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
        "--min-position",
        type=int,
        default=None,
        help=(
            "Minimum focuser position to keep. Must be used with "
            "--max-position. Overrides configuration for one execution."
        ),
    )
    parser.add_argument(
        "--max-position",
        type=int,
        default=None,
        help=(
            "Maximum focuser position to keep. Must be used with "
            "--min-position. Overrides configuration for one execution."
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
        type=float,
        default=None,
        help=(
            "Minimum X-axis limit. Must be used with --x-max. "
            "Overrides calculated range for the chart only."
        ),
    )
    parser.add_argument(
        "--x-max",
        type=float,
        default=None,
        help=(
            "Maximum X-axis limit. Must be used with --x-min. "
            "Overrides calculated range for the chart only."
        ),
    )
    parser.add_argument(
        "--y-min",
        type=float,
        default=-10,
        help="Minimum Y axis limit. Default: -10",
    )
    parser.add_argument(
        "--y-max",
        type=float,
        default=40,
        help="Maximum Y axis limit. Default: 40",
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
        type=float,
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
        type=float,
        default=3.0,
        help="Absolute studentized residual threshold. Default: 3.0",
    )
    return parser.parse_args()


def validate_center_and_range(focus_center: int, focus_range: int, source: str):
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


def get_default_config_path() -> Path:
    return Path(__file__).resolve().with_name(CONFIG_FILENAME)


def load_tube_focus_config(config_path: Path, tube: str, defaults: dict):
    """Load optional per-tube center/range configuration.

    Missing config files or missing tube sections are non-fatal: built-in
    defaults are retained. Invalid configured numeric values raise ValueError,
    so a typo cannot silently contaminate the regression.
    """
    values = {
        "focus_center": defaults["focus_center"],
        "focus_range": defaults["focus_range"],
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
    except ValueError as error:
        raise ValueError(
            f"Invalid numeric focus configuration in [{tube}] "
            f"of {config_path}: {error}"
        ) from error

    validate_center_and_range(
        values["focus_center"],
        values["focus_range"],
        f"Configuration [{tube}] in {config_path}",
    )
    values["source"] = str(config_path)
    return values


def resolve_focus_interval(args, tube_defaults: dict, config_values: dict):
    """Resolve filtering and X-axis limits using documented precedence.

    Precedence:
      1. Explicit --min-position plus --max-position.
      2. Temporary --focus-center plus optional --focus-range.
      3. Per-tube focus_config.properties values.
      4. Built-in TUBE_DEFAULTS values.
    """
    manual_min_max = (
        args.min_position is not None or args.max_position is not None
    )
    manual_x_axis = args.x_min is not None or args.x_max is not None

    if manual_min_max and (
        args.min_position is None or args.max_position is None
    ):
        raise ValueError(
            "--min-position and --max-position must be specified together."
        )

    if manual_x_axis and (args.x_min is None or args.x_max is None):
        raise ValueError("--x-min and --x-max must be specified together.")

    if args.focus_range is not None and args.focus_center is None:
        raise ValueError("--focus-range requires --focus-center.")

    if args.focus_center is not None and manual_min_max:
        raise ValueError(
            "--focus-center cannot be combined with --min-position or "
            "--max-position."
        )

    if args.focus_center is not None:
        focus_center = args.focus_center
        focus_range = (
            config_values["focus_range"]
            if args.focus_range is None
            else args.focus_range
        )
        interval_source = "command-line focus center/range override"
    elif manual_min_max:
        min_position = args.min_position
        max_position = args.max_position
        if min_position >= max_position:
            raise ValueError(
                f"--min-position ({min_position}) must be strictly less than "
                f"--max-position ({max_position})."
            )

        if manual_x_axis:
            x_min = args.x_min
            x_max = args.x_max
            if x_min >= x_max:
                raise ValueError(
                    f"--x-min ({x_min}) must be strictly less than "
                    f"--x-max ({x_max})."
                )
        else:
            x_min = float(min_position)
            x_max = float(max_position)

        return {
            "focus_center": (min_position + max_position) // 2,
            "focus_range": max_position - min_position,
            "min_position": min_position,
            "max_position": max_position,
            "x_min": x_min,
            "x_max": x_max,
            "source": "manual --min-position/--max-position override",
        }
    else:
        focus_center = config_values["focus_center"]
        focus_range = config_values["focus_range"]
        interval_source = config_values["source"]

    min_position, max_position = validate_center_and_range(
        focus_center,
        focus_range,
        interval_source,
    )

    if manual_x_axis:
        x_min = args.x_min
        x_max = args.x_max
        if x_min >= x_max:
            raise ValueError(
                f"--x-min ({x_min}) must be strictly less than "
                f"--x-max ({x_max})."
            )
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
    if last_days is None:
        return None
    if last_days < 1:
        raise ValueError("--last-days must be 1 or greater.")
    today = datetime.now().date()
    return today - timedelta(days=last_days - 1)


def get_log_files(log_path: Path, start_date):
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
    match = re.search(r"Log_(\d{4}-\d{2}-\d{2})T", file_path.name)
    if match:
        return match.group(1)
    return datetime.fromtimestamp(file_path.stat().st_mtime).strftime(
        "%Y-%m-%d"
    )


def parse_logs(log_files, min_position: int, max_position: int, start_date):
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
    name = output_csv.name.replace(
        "sharpcap_data_focus",
        "sharpcap_synthetic_data_focus",
    )
    return output_csv.with_name(name)


def load_synthetic_csv(
    output_csv: Path,
    min_position: int,
    max_position: int,
) -> list:
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
    with output_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fieldnames,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(results)


def write_state_json(results, output_json: Path, inverse_slope, slope, intercept):
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
    interval = resolve_focus_interval(args, tube_defaults, config_values)

    tube_label = tube_defaults["label"]
    min_position = interval["min_position"]
    max_position = interval["max_position"]
    x_min = interval["x_min"]
    x_max = interval["x_max"]

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

    remove_outliers = not args.no_remove_outliers
    start_date = get_start_date(args.last_days)

    log_path = Path(args.log_path)
    output_csv = Path(output_csv_name).resolve()
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