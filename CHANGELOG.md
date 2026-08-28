# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- `.gitignore`: `sharpcap_final_focus.csv`, `sharpcap_removed_outliers.csv`, and
  `sharpcap_focus_temperature.png` are now ignored. These files are regenerated on
  every run and should not be tracked by git.

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
- Generates `sharpcap_final_focus.csv` and `sharpcap_removed_outliers.csv`.
- Generates `sharpcap_focus_temperature.png` — scatter plot with regression line.
- `--predict-temperature` option: predicts the focus position for a given temperature.
- `--auto-axis` option: automatic axis scaling based on data range.
- `--last-days` option: limits analysis to the most recent N days of data.
