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

Features
--------
- Parses SharpCap log files and extracts autofocus results.
- Filters results by calendar days and focuser step range.
- Optionally removes outliers using studentized residuals.
- Fits a linear regression between focuser position and temperature.
- Predicts focuser position for a target temperature.
- Exports cleaned results and removed outliers to CSV.
- Exports last valid autofocus reference and regression model to JSON.
- Generates a chart with regression and two side summary tables.
- Supports two optical tubes via --tube {main,guide}:
    main  — C8 + ASI2600MC Pro  (~25 000 steps, state JSON: sharpcap_focus_state.json)
    guide — 50ED + ASI224MC     (~350 000 steps, state JSON: sharpcap_focus_state_guide.json)
- Automatically loads synthetic data if a matching CSV exists beside the output CSV.
  Synthetic points are:
    * merged with real data before regression.
    * immune to outlier removal.
    * plotted in green with a distinct legend entry.
    * excluded from the output CSV and from the state JSON reference.

Author
------
David Gonzalez Lopez-Tercero

Contact
-------
Email: davidglt@dragonit.es
Website: https://dragonit.es

Date
----
2026-08-30

License
-------
GPL-3.0-or-later
"""

import argparse
import csv
import json
import math
import re
from datetime import datetime, timedelta
from pathlib import Path


DEG_C_CHART = "\u00B0C"
DEG_C_CONSOLE = "\u00BAC"

TUBE_DEFAULTS = {
    "main": {
        "label": "Main tube C8",
        "min_position": 24000,
        "max_position": 27000,
        "x_min": 24000.0,
        "x_max": 27000.0,
        "output_csv": "sharpcap_data_focus.csv",
        "output_state": "sharpcap_focus_state.json",
        "chart_name": "sharpcap_focus_temperature.png",
    },
    "guide": {
        "label": "Guide tube 50ED",
        "min_position": 330000,
        "max_position": 370000,
        "x_min": 330000.0,
        "x_max": 370000.0,
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
      "Optical tube to analyse. Selects per-tube defaults for position "
      "range, output file names, and chart title. "
      "'main' = C8 + ASI2600MC Pro (~25 000 steps). "
      "'guide' = 50ED + ASI224MC (~350 000 steps). "
      "Individual flags (--min-position, --output-state-json, etc.) "
      "always override the tube defaults. Default: main"
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
    help="Minimum focuser position to keep. Default depends on --tube.",
  )
  parser.add_argument(
    "--max-position",
    type=int,
    default=None,
    help="Maximum focuser position to keep. Default depends on --tube.",
  )
  parser.add_argument(
    "--x-min",
    type=float,
    default=None,
    help="Minimum X axis limit. Default depends on --tube.",
  )
  parser.add_argument(
    "--x-max",
    type=float,
    default=None,
    help="Maximum X axis limit. Default depends on --tube.",
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
    help="Predict focuser position for this temperature in ºC.",
  )
  parser.add_argument(
    "--no-remove-outliers",
    action="store_true",
    help="Disable outlier removal. By default, outliers are removed using externally studentized residuals.",
  )
  parser.add_argument(
    "--studentized-threshold",
    type=float,
    default=3.0,
    help="Absolute studentized residual threshold. Default: 3.0",
  )
  return parser.parse_args()


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
  return [
    file for file in log_files
    if datetime.fromtimestamp(file.stat().st_mtime).date() >= start_date
  ]


def extract_date_from_filename(file_path: Path) -> str:
  match = re.search(r"Log_(\d{4}-\d{2}-\d{2})T", file_path.name)
  if match:
    return match.group(1)
  return datetime.fromtimestamp(file_path.stat().st_mtime).strftime("%Y-%m-%d")


def parse_logs(log_files, min_position: int, max_position: int, start_date):
  time_regex = re.compile(
    r"^(?:Info|Debug|Warning|Error)\s+(?P<time>\d{1,2}:\d{2}:\d{2})(?:\.\d+)?"
  )
  autofocus_regex = re.compile(
    r"Autofocus result\s*:\s*best focus at\s+(?P<position>-?\d+(?:[.,]\d+)?)\s+with focuser temperature of\s+(?P<temperature>-?\d+(?:[.,]\d+)?)\s*C",
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
        position = float(autofocus_match.group("position").replace(",", "."))
        if position < min_position or position > max_position:
          continue
        temperature = float(autofocus_match.group("temperature").replace(",", "."))
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
  name = output_csv.name.replace("sharpcap_data_focus", "sharpcap_synthetic_data_focus")
  return output_csv.with_name(name)


def load_synthetic_csv(output_csv: Path) -> list:
  path = derive_synthetic_csv_path(output_csv)
  if not path.exists():
    return []
  rows = []
  with path.open("r", encoding="utf-8") as handle:
    reader = csv.DictReader(handle)
    for row in reader:
      rows.append(
        {
          "DateTime": row["DateTime"].strip(),
          "TemperatureC": round(float(row["TemperatureC"]), 2),
          "FocuserSteps": round(float(row["FocuserSteps"])),
          "_synthetic": True,
        }
      )
  print(f"Synthetic CSV found: {path} ({len(rows)} points)")
  return rows


def filter_outliers_studentized(results, threshold: float):
  import numpy as np
  import statsmodels.api as sm

  if len(results) < 5:
    return results, [], 0

  x = np.array([row["FocuserSteps"] for row in results], dtype=float)
  y = np.array([row["TemperatureC"] for row in results], dtype=float)
  if len(np.unique(x)) < 2:
    return results, [], 0

  design_matrix = sm.add_constant(x)
  model = sm.OLS(y, design_matrix).fit()
  influence = model.get_influence()
  studentized = influence.resid_studentized_external

  filtered = []
  removed = []
  for row, residual in zip(results, studentized):
    if row.get("_synthetic"):
      filtered.append(row)
      continue
    row_with_diagnostic = row.copy()
    row_with_diagnostic["StudentizedResidual"] = round(float(residual), 3)
    if abs(residual) > threshold:
      row_with_diagnostic["Reason"] = f"Studentized residual > {threshold:.1f}"
      removed.append(row_with_diagnostic)
    else:
      filtered.append(row)
  return filtered, removed, len(removed)


def write_csv(results, output_csv: Path, fieldnames):
  with output_csv.open("w", newline="", encoding="utf-8") as handle:
    writer = csv.DictWriter(handle, fieldnames=fieldnames)
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
    "model_tcf": None if inverse_slope is None else round(float(inverse_slope), 2),
    "model_inv_tcf": None if slope is None else round(float(slope), 6),
    "model_intercept_c": None if intercept is None else round(float(intercept), 3),
  }
  with output_json.open("w", encoding="utf-8") as handle:
    json.dump(state, handle, indent=2, ensure_ascii=False)
    handle.write("\n")
  return True


def style_table(table, fontsize=7.0, header_height=0.058, row_height=0.050):
  table.auto_set_font_size(False)
  table.set_fontsize(fontsize)
  for (row, col), cell in table.get_celld().items():
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
  synthetic_count: int,
  real_count: int,
):
  import matplotlib
  matplotlib.use("Agg")
  import matplotlib.pyplot as plt
  import numpy as np

  DC = DEG_C_CHART
  real_results = [row for row in results if not row.get("_synthetic")]
  synth_results = [row for row in results if row.get("_synthetic")]

  x_all = np.array([row["FocuserSteps"] for row in results], dtype=float)
  y_all = np.array([row["TemperatureC"] for row in results], dtype=float)
  x_real = np.array([row["FocuserSteps"] for row in real_results], dtype=float)
  y_real = np.array([row["TemperatureC"] for row in real_results], dtype=float)

  fig = plt.figure(figsize=(11.2, 6.4))
  ax = fig.add_axes([0.08, 0.16, 0.58, 0.74])
  info_ax = fig.add_axes([0.70, 0.12, 0.28, 0.78])
  info_ax.axis("off")

  scatter = ax.scatter(x_real, y_real, color="navy", s=38, label="Autofocus results", zorder=3)

  synth_scatter = None
  if synth_results:
    x_synth = np.array([row["FocuserSteps"] for row in synth_results], dtype=float)
    y_synth = np.array([row["TemperatureC"] for row in synth_results], dtype=float)
    synth_scatter = ax.scatter(
      x_synth, y_synth, color="green", s=38, marker="s", label="Synthetic data", zorder=3
    )

  outlier_scatter = None
  outlier_x = None
  if removed_outliers:
    outlier_x = np.array([row["FocuserSteps"] for row in removed_outliers], dtype=float)
    outlier_y = np.array([row["TemperatureC"] for row in removed_outliers], dtype=float)
    outlier_scatter = ax.scatter(
      outlier_x, outlier_y, s=72, facecolors="none", edgecolors="darkorange",
      linewidths=1.5, label="Removed outliers", zorder=4
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
    solid_line, = ax.plot(solid_x, solid_y, color="red", linewidth=1.9, linestyle="-", zorder=2)

    if axis_x_min < data_x_min:
      left_x = np.linspace(axis_x_min, data_x_min, 80)
      left_y = slope * left_x + intercept
      ax.plot(left_x, left_y, color="red", linewidth=1.6, linestyle="--", zorder=1)
    if axis_x_max > data_x_max:
      right_x = np.linspace(data_x_max, axis_x_max, 80)
      right_y = slope * right_x + intercept
      ax.plot(right_x, right_y, color="red", linewidth=1.6, linestyle="--", zorder=1)

    dashed_proxy, = ax.plot([], [], color="red", linewidth=1.6, linestyle="--")

    if predict_temperature is not None and not math.isclose(slope, 0.0, abs_tol=1e-12):
      predicted_steps = (predict_temperature - intercept) / slope
      predicted_steps_rounded = round(predicted_steps)
      prediction_marker = ax.scatter([predicted_steps], [predict_temperature], color="green", s=70, marker="D", zorder=5)
      ax.annotate(
        f"{predicted_steps_rounded} steps @ {predict_temperature:.2f} {DC}",
        xy=(predicted_steps, predict_temperature), xytext=(10, 10), textcoords="offset points",
        fontsize=8, color="darkgreen",
        bbox=dict(boxstyle="round", facecolor="white", alpha=0.85),
        arrowprops=dict(arrowstyle="->", color="darkgreen"),
      )

  ax.set_title(f"Focuser Position vs Temperature — {tube_label}", fontsize=11)
  ax.set_xlabel("s: Focuser Steps", fontsize=9.5, labelpad=6)
  ax.set_ylabel(f"T: Temperature ({DC})", fontsize=9.5)
  ax.tick_params(axis="both", labelsize=8.5)
  ax.grid(True, alpha=0.3)

  if auto_axis:
    ax.autoscale(enable=True, axis="both", tight=False)
    ax.margins(x=0.05, y=0.08)
  else:
    ax.set_xlim(x_min, x_max)
    ax.set_ylim(y_min, y_max)

  # Legend — show point counts for autofocus results and removed outliers
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
    labels.append(f"Prediction at {predict_temperature:.2f} {DC}")

  legend = info_ax.legend(handles, labels, loc="upper left", frameon=True, borderpad=0.5, labelspacing=0.5, fontsize=8)

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
    ["T", f"Temperature ({DC})"],
    ["s", "Focuser Steps"],
  ]
  if predict_temperature is not None:
    model_rows.append(["Target T", f"{predict_temperature:.2f} {DC}"])
  model_rows.append([f"k ({DC}/step)", f"{slope:.6f}" if slope is not None else "-"])
  model_rows.append([f"TCF = 1/k (step/{DC})", f"{inverse_slope:.2f}" if inverse_slope is not None else "-"])
  model_rows.append([f"b ({DC})", f"{intercept:.3f}" if intercept is not None else "-"])
  if predicted_steps_rounded is not None:
    model_rows.append(["Focus(T)", f"{predicted_steps_rounded} steps"])

  # Focus table — real pts first, then positional data, then diagnostics
  focus_rows = [["Real pts", str(real_count)]]
  if first_focus is not None:
    focus_rows.append(["First focus", f"{first_focus} steps"])
  if last_focus is not None:
    focus_rows.append(["Last focus", f"{last_focus} steps"])
  if focus_span_steps is not None:
    focus_rows.append(["Delta focus", f"{focus_span_steps:+d} steps"])
  if synthetic_count > 0:
    focus_rows.append(["Synthetic pts", str(synthetic_count)])
  if outlier_mode_enabled:
    focus_rows.append(["Outliers", f"{len(removed_outliers)} removed"])
    focus_rows.append(["Threshold", f"|t| > {studentized_threshold:.1f}"])
  else:
    focus_rows.append(["Outliers", "Disabled"])
  if auto_axis:
    focus_rows.append(["X axis", "Auto (steps)"])
    focus_rows.append(["Y axis", f"Auto ({DC})"])
  else:
    focus_rows.append(["X axis", f"{x_min:.0f} to {x_max:.0f} steps"])
    focus_rows.append(["Y axis", f"{y_min:.0f} to {y_max:.0f} {DC}"])

  info_ax.text(0.01, 0.690, "Model", fontsize=9, fontweight="bold", ha="left", va="bottom")
  info_ax.text(0.01, 0.365, "Focus", fontsize=9, fontweight="bold", ha="left", va="bottom")

  model_table = info_ax.table(
    cellText=model_rows, colLabels=["Item", "Value"], colLoc="left", cellLoc="left",
    colWidths=[0.41, 0.55], bbox=[0.01, 0.465, 0.96, 0.22],
  )
  style_table(model_table, fontsize=7.0, header_height=0.058, row_height=0.050)

  focus_table = info_ax.table(
    cellText=focus_rows, colLabels=["Item", "Value"], colLoc="left", cellLoc="left",
    colWidths=[0.42, 0.54], bbox=[0.01, 0.11, 0.96, 0.25],
  )
  style_table(focus_table, fontsize=7.0, header_height=0.058, row_height=0.050)

  fig.savefig(chart_path, dpi=130, bbox_inches="tight", bbox_extra_artists=(legend,))
  plt.close(fig)
  return slope, intercept, inverse_slope, predicted_steps_rounded


def main():
  args = parse_arguments()

  td = TUBE_DEFAULTS[args.tube]
  tube_label = td["label"]
  min_position = args.min_position if args.min_position is not None else td["min_position"]
  max_position = args.max_position if args.max_position is not None else td["max_position"]
  x_min = args.x_min if args.x_min is not None else td["x_min"]
  x_max = args.x_max if args.x_max is not None else td["x_max"]
  output_csv_name = args.output_csv if args.output_csv is not None else td["output_csv"]
  output_state_name = args.output_state_json if args.output_state_json is not None else td["output_state"]

  remove_outliers = not args.no_remove_outliers
  start_date = get_start_date(args.last_days)

  log_path = Path(args.log_path)
  output_csv = Path(output_csv_name).resolve()
  outliers_csv = output_csv.with_name(
    output_csv.stem.replace("sharpcap_data_focus", "sharpcap_removed_outliers") + output_csv.suffix
  )
  chart_path = output_csv.with_name(td["chart_name"])
  state_json = Path(output_state_name).resolve()

  log_files = get_log_files(log_path, start_date)
  real_results = parse_logs(log_files, min_position, max_position, start_date)
  synthetic_rows = load_synthetic_csv(output_csv)

  combined = real_results + synthetic_rows
  combined.sort(key=lambda item: item["DateTime"])

  original_count = len(real_results)
  removed_outliers = []
  removed_count = 0

  if remove_outliers and combined:
    combined, removed_outliers, removed_count = filter_outliers_studentized(combined, args.studentized_threshold)

  real_clean = [row for row in combined if not row.get("_synthetic")]

  write_csv(real_clean, output_csv, ["DateTime", "TemperatureC", "FocuserSteps"])
  print(f"CSV created: {output_csv}")

  if remove_outliers:
    write_csv(
      removed_outliers,
      outliers_csv,
      ["DateTime", "TemperatureC", "FocuserSteps", "StudentizedResidual", "Reason"],
    )
    print(f"Outliers CSV created: {outliers_csv}")
    print(f"Outliers written: {len(removed_outliers)}")

  synthetic_count = len(synthetic_rows)
  print(f"Tube: {tube_label}")
  print(f"Extracted autofocus results: {original_count}")
  print(f"Remaining points after filters: {len(real_clean)} real + {synthetic_count} synthetic")

  if start_date is not None:
    print(f"Calendar-day filter start: {start_date.isoformat()}")

  if remove_outliers:
    print(f"Outliers removed: {removed_count}")
    print("Outlier method: externally studentized residuals " f"(|t| > {args.studentized_threshold:.1f})")
  else:
    print("Outlier removal: disabled")

  if args.auto_axis:
    print("Axis mode: automatic")
  else:
    print(f"X axis limits: {x_min} to {x_max} steps")
    print(f"Y axis limits: {args.y_min} to {args.y_max} {DEG_C_CONSOLE}")

  if real_clean:
    first_focus = real_clean[0]["FocuserSteps"]
    last_focus = real_clean[-1]["FocuserSteps"]
    focus_span_steps = last_focus - first_focus
    print(f"First focus: {first_focus} steps")
    print(f"Last focus: {last_focus} steps")
    print(f"Delta focus: {focus_span_steps:+d} steps")

    slope, intercept, inverse_slope, predicted_steps_rounded = create_chart(
      combined, removed_outliers, chart_path, args.predict_temperature,
      args.studentized_threshold, remove_outliers, x_min, x_max,
      args.y_min, args.y_max, args.auto_axis, tube_label, synthetic_count,
      real_count=len(real_clean),
    )

    if slope is not None and intercept is not None:
      print(f"Regression equation: T = {slope:.6f} * Steps + {intercept:.3f}")
      print(f"k = {slope:.6f} {DEG_C_CONSOLE}/step")
      if inverse_slope is not None:
        print(f"TCF = 1/k = {inverse_slope:.2f} steps/{DEG_C_CONSOLE}")
      print(f"b = {intercept:.3f} {DEG_C_CONSOLE}")
      if predicted_steps_rounded is not None and args.predict_temperature is not None:
        print(f"Predicted focus for {args.predict_temperature:.2f} {DEG_C_CONSOLE}: {predicted_steps_rounded} steps")
    else:
      print("Regression could not be calculated with the available points.")

    state_written = write_state_json(combined, state_json, inverse_slope, slope, intercept)
    if state_written:
      print(f"State JSON created: {state_json}")
      print(f"Reference autofocus timestamp: {real_clean[-1]['DateTime']}")
      print(f"Reference temperature: {real_clean[-1]['TemperatureC']:.2f} {DEG_C_CONSOLE}")
      print(f"Reference focus: {real_clean[-1]['FocuserSteps']} steps")
    else:
      print("State JSON was not created: no valid autofocus reference available.")

    print(f"Chart created: {chart_path}")
  else:
    print("Chart was not created: no real autofocus results were found.")


if __name__ == "__main__":
  main()
