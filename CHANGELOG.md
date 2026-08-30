# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Automatic synthetic-data detection by convention:
  - `sharpcap_data_focus.csv`       → `sharpcap_synthetic_data_focus.csv`
  - `sharpcap_data_focus_guide.csv` → `sharpcap_synthetic_data_focus_guide.csv`
- `sharpcap_synthetic_data_focus.csv`: example synthetic dataset for the
  main tube (C8), covering a full annual temperature cycle.
- `sharpcap_synthetic_data_focus_guide.csv`: example synthetic dataset for
  the guide tube (50ED), covering a full annual temperature cycle.
- Synthetic points are plotted in **green squares** with a dedicated legend
  entry showing the point count.
- Console message when a synthetic CSV is detected and loaded.

### Changed

- Synthetic data no longer uses a command-line parameter; if the matching
  synthetic CSV exists, it is applied automatically, otherwise it is ignored.
- Outlier filter (`filter_outliers_studentized`) skips rows flagged as
  synthetic (`_synthetic=True`) unconditionally — they can never be removed.
- Output CSV (`sharpcap_data_focus[_guide].csv`) contains only real cleaned
  data; synthetic rows are never written to it.
- State JSON reference (`focus_ref`, `temp_ref`, `timestamp_ref`) is always
  derived from the last **real** autofocus point, not a synthetic one.
- Regression uses all combined points (real + synthetic) so synthetic data
  influences the model permanently.
- `.gitignore` updated: `sharpcap_synthetic_data_focus*.csv` added so local
  production overrides are never accidentally committed.

### Fixed

- CSV output file names renamed for clarity:
    - `sharpcap_final_focus.csv`       → `sharpcap_data_focus.csv`
    - `sharpcap_final_focus_guide.csv` → `sharpcap_data_focus_guide.csv`
  Updated in `TUBE_DEFAULTS`, `main()` (outliers CSV stem derivation),
  README output tables, and CHANGELOG.
- `TUBE_DEFAULTS` guide tube: position range widened from 330 000–340 000 to
  320 000–360 000 steps (`min_position`, `max_position`, `x_min`, `x_max`).

## [1.2.0] - 2026-08-30

### Added

- `--tube {main,guide}` option: selects per-tube defaults for focuser position
  range, output file names, and chart title.  Individual flags always override
  tube defaults.
    - `main`  (default) — C8 + ASI2600MC Pro, ~25 000 steps:
        - `--min-position` 24 000 / `--max-position` 27 000
        - `--output-state-json` `sharpcap_focus_state.json`
        - `--output-csv` `sharpcap_data_focus.csv`
        - chart file: `sharpcap_focus_temperature.png`
        - chart title: *Focuser Position vs Temperature — Main tube C8*
    - `guide` — 50ED + ASI224MC, ~335 000 steps:
        - `--min-position` 320 000 / `--max-position` 360 000
        - `--output-state-json` `sharpcap_focus_state_guide.json`
        - `--output-csv` `sharpcap_data_focus_guide.csv`
        - chart file: `sharpcap_focus_temperature_guide.png`
        - chart title: *Focuser Position vs Temperature — Guide tube 50ED*
- `TUBE_DEFAULTS` dict: consolidates all per-tube default values in one place.
- Chart title now includes the tube label (`tube_label` parameter passed to
  `create_chart()`).
- README: *Sister repository* section added, documenting the sibling-directory
  relationship with `sharpcap-focus-sequencer` and the canonical two-repository
  layout. `<any-parent>\` is used instead of a hardcoded absolute path to reflect
  that the installation path is unrestricted.
- README: *Typical workflow* step 5 updated to mention the sequencer as the
  official consumer of `sharpcap_focus_state.json`.

### Changed

- `parse_arguments()`: `--min-position`, `--max-position`, `--x-min`, `--x-max`,
  `--output-csv`, and `--output-state-json` now default to `None`; their effective
  values are resolved from `TUBE_DEFAULTS[args.tube]` in `main()` unless explicitly
  overridden on the command line.
- `main()`: per-tube default resolution added before path construction.
- `create_chart()`: new `tube_label` parameter added; chart title updated to
  `f"Focuser Position vs Temperature — {tube_label}"`.
- Outliers CSV name now derived from the main CSV stem for consistency:
  `sharpcap_removed_outliers.csv` (main) / `sharpcap_removed_outliers_guide.csv` (guide).
- `.gitignore`: `sharpcap_data_focus.csv`, `sharpcap_removed_outliers.csv`, and
  `sharpcap_focus_temperature.png` are now ignored. These files are regenerated on
  every run and should not be tracked by git.

### Removed

- `run_focuser.bat`: superseded by `resolve_producer_python()` in the sibling
  `sharpcap-focus-sequencer`. The sequencer now locates and invokes
  `sharpcap_focuser.py` directly using the sibling `.venv` Python interpreter.
  For a manual or diagnostic run, call `sharpcap_focuser.py` directly from the
  command line using the virtual environment:
  ```
  .venv\Scripts\python.exe sharpcap_focuser.py
  ```

## [1.1.0] - 2026-08-25

### Added

- `--output-state-json` option: exports the regression model and last autofocus
  reference to a JSON file (`sharpcap_focus_state.json`) for use by external
  tools such as `sharpcap-focus-sequencer`.
- `write_state_json()` function that serialises the following fields:
  - `timestamp_ref` — datetime of the reference autofocus point
  - `temp_ref` — temperature at the reference autofocus point (°C)
  - `focus_ref` — focuser position at the reference autofocus point (steps)
  - `last_temp_applied` — temperature at which the last correction was applied (°C)
  - `last_focus_applied` — focuser position at the last correction (steps)
  - `model_tcf` — temperature compensation factor (steps/°C)
  - `model_inv_tcf` — inverse TCF (°C/step), slope k of the regression
  - `model_intercept_c` — intercept b of the regression
- `sharpcap_focus_state.json` added to `.gitignore` (runtime artifact).
- README updated with full JSON state file documentation, field table,
  updated options table, new usage example, and extended workflow.

## [1.0.0] - 2026-08-01

### Added

- Initial release.
- Reads SharpCap autofocus log files and extracts focus/temperature pairs.
- Removes outliers using studentised residuals (statsmodels).
- Fits a linear regression model (focus vs. temperature).
- Generates `sharpcap_data_focus.csv` and `sharpcap_removed_outliers.csv`.
- Generates `sharpcap_focus_temperature.png` — scatter plot with regression line.
- `--predict-temperature` option: predicts the focus position for a given temperature.
- `--auto-axis` option: automatic axis scaling based on data range.
- `--last-days` option: limits analysis to the most recent N days of data.
