# SharpCap Autofocus Log Extractor and Temperature Regression Plotter

Python script for extracting autofocus results from SharpCap logs, filtering and
cleaning the data, and generating a CSV export, a regression chart that relates
focuser position to temperature, and a JSON state file with the latest autofocus
reference and the fitted thermal model.

Supports two optical tubes via `--tube {main,guide}`:

| Tube | Hardware | EAF position range | Typical position | State JSON |
|---|---|---|---|---|
| `main` (default) | C8 + ASI2600MC Pro | 24 000 – 27 000 steps | ~25 000 | `sharpcap_focus_state.json` |
| `guide` | 50ED + ASI224MC | 315 000 – 365 000 steps | ~347 000 | `sharpcap_focus_state_guide.json` |

> **Note on guide tube range:** the 50ED EAF (ASCOM.EAF_2.Focuser) is configured
> with **Max Steps = 520 000** in ASICap → Focuser → Advanced, but autofocus
> results during normal operation fall in the 315 000 – 365 000 step window.
> Use `--min-position` / `--max-position` to override these defaults if your
> setup differs.

## What it does

- Reads SharpCap `Log_*.log` files.
- Extracts autofocus results with timestamp, temperature, and best focus position.
- Filters data by focuser step range (per-tube defaults, overridable).
- Filters data by the last N natural calendar days.
- Optionally removes outliers using externally studentized residuals.
- Fits a linear regression between temperature and focuser position.
- Calculates the inverse slope as **TCF** (temperature compensation factor, in steps/°C).
- Predicts focuser position for a target temperature.
- Exports cleaned results to CSV.
- Exports removed outliers to a separate CSV.
- Exports the last valid autofocus reference and the regression model to a JSON
  state file, ready to be consumed by an external sequencer or automation script.
- Generates a chart with regression, prediction marker, legend, and summary tables.
- Chart title includes the tube label (e.g. *Focuser Position vs Temperature — Guide tube 50ED*).

## Main outputs

### Main tube (`--tube main`, default)

| File | Description |
|---|---|
| `sharpcap_data_focus.csv` | Filtered autofocus results. |
| `sharpcap_removed_outliers.csv` | Removed outliers with studentized residual diagnostics. |
| `sharpcap_focus_temperature.png` | Plot with regression line, prediction marker, and summary tables. |
| `sharpcap_focus_state.json` | Last valid autofocus reference plus the fitted thermal model. |

### Guide tube (`--tube guide`)

| File | Description |
|---|---|
| `sharpcap_data_focus_guide.csv` | Filtered autofocus results. |
| `sharpcap_removed_outliers_guide.csv` | Removed outliers with studentized residual diagnostics. |
| `sharpcap_focus_temperature_guide.png` | Plot with regression line, prediction marker, and summary tables. |
| `sharpcap_focus_state_guide.json` | Last valid autofocus reference plus the fitted thermal model. |

## Regression model

The chart uses the symbolic linear model:

```text
T = k·s + b
```

Where:

- `T` = Temperature (°C)
- `s` = Focuser Steps
- `k` = slope in °C/step (called `slope` in the source code)
- `TCF = 1/k` = temperature compensation factor in steps/°C (called `inverse_slope` in the source code)
- `b` = intercept in °C

> **Note on TCF sign:** for the C8 + F/6.3 reducer and the 50ED, the TCF is
> **negative** (focus moves inward — fewer steps — as temperature rises).
> Typical values: main tube ~−62 steps/°C, guide tube ~−850 steps/°C.
> A positive TCF would mean the focuser needs to move outward as it warms up,
> which is uncommon for these optical designs.

## Synthetic data (Bayesian prior)

When real autofocus observations are scarce — especially at the start of a new
season or for a newly commissioned tube — the regression model can be anchored
with **synthetic data**: a set of plausible focuser-position/temperature pairs
derived from prior knowledge of the optical tube’s thermal behaviour.

Synthetic points act as a **weak Bayesian prior**: they constrain the regression
slope and intercept while real data is accumulating, and they are gradually
outnumbered and overridden as more real observations are collected.

### File naming convention

Place the synthetic CSV **in the same folder** as the output CSV, using the
following names:

| Tube | Synthetic CSV |
|---|---|
| `main` | `sharpcap_synthetic_data_focus.csv` |
| `guide` | `sharpcap_synthetic_data_focus_guide.csv` |

The script detects these files automatically at runtime — no flag is needed.

### CSV format

The synthetic CSV must have the same three columns as the real data CSV:

```text
DateTime,TemperatureC,FocuserSteps
```

### Behaviour

- Synthetic points are **merged with real data** before regression.
- They are **evaluated by the same outlier filter** as real points: if enough
  real data accumulates to push a synthetic point beyond the studentized
  residual threshold, it is expelled automatically, letting real observations
  dominate the model naturally.
- They are **plotted in green** with a distinct legend entry (*Synthetic data*).
- They are **excluded from the output CSV** (`sharpcap_data_focus*.csv`) and
  from the state JSON reference — only real measurements are written there.
- If a synthetic point is expelled as an outlier, it appears in the
  removed-outliers CSV with `Synthetic = yes`.

### Realistic variance

Synthetic points should include a small random scatter (σ ≈ 500–1 000 steps)
around the expected regression line. A perfectly collinear set of synthetic
points compresses the model’s residual variance artificially, which can cause
real observations that lie slightly off the line to be incorrectly flagged as
outliers by the studentized residual filter.

### Example — guide tube (50ED + ASI224MC)

The following table covers one representative observation per month across the
full temperature range of the site, with ±800-step Gaussian noise added to each
point to simulate realistic EAF scatter:

```csv
DateTime,TemperatureC,FocuserSteps
2026-01-01 22:00:00,3.5,358745
2026-02-01 21:30:00,4.5,357235
2026-03-01 22:00:00,8.5,353851
2026-04-01 21:00:00,11.0,352043
2026-05-01 22:00:00,14.5,347128
2026-06-01 22:00:00,21.0,340608
2026-07-01 23:00:00,34.0,329019
2026-08-01 22:30:00,30.0,332381
2026-09-01 21:00:00,17.0,344432
2026-10-01 20:00:00,12.0,350256
2026-11-01 19:00:00,7.0,354467
2026-12-01 19:00:00,4.0,357474
```

Copy this table into `sharpcap_synthetic_data_focus_guide.csv` (alongside
`sharpcap_data_focus_guide.csv`) to bootstrap the guide tube model before
collecting enough real sessions.

> **Delete or empty the synthetic CSV** once you have accumulated 15–20 real
> autofocus results that cover the seasonal temperature range. At that point the
> real data is sufficient to fit a reliable model on its own.

## JSON state file

After each successful run the script writes the state JSON (path depends on `--tube`).
This file is the **single source of truth** for the thermal model and the latest
focus reference:

```json
{
  "timestamp_ref": "2026-08-24 23:11:32",
  "temp_ref": 18.4,
  "focus_ref": 25342,
  "last_temp_applied": 18.4,
  "last_focus_applied": 25342,
  "model_tcf": -61.59,
  "model_inv_tcf": -0.016237,
  "model_intercept_c": 889.541
}
```

| Field | Description |
|---|---|
| `timestamp_ref` | Datetime of the last clean autofocus result used as reference. |
| `temp_ref` | Focuser temperature (°C) at that reference point. |
| `focus_ref` | Focuser position (steps) at that reference point. |
| `last_temp_applied` | Temperature at which the last correction was applied (initially equal to `temp_ref`; can be overwritten by the sequencer at runtime). |
| `last_focus_applied` | Focuser position of the last applied correction (initially equal to `focus_ref`). |
| `model_tcf` | TCF = 1/k (steps/°C). **Negative for these tubes**: focus moves inward (fewer steps) as temperature rises. |
| `model_inv_tcf` | k = slope (°C/step). Negative for refractors and most reflectors with focal reducers. |
| `model_intercept_c` | Regression intercept b (°C). |

> **Note:** `last_temp_applied` and `last_focus_applied` are intentionally
> separate from `temp_ref` / `focus_ref` so that an external sequencer can update
> them at runtime to track the current compensation state without losing the
> original reference.

## Sister repository

This repository works alongside
[sharpcap-focus-sequencer](https://github.com/davidglt/sharpcap-focus-sequencer)
as two **sibling repositories** cloned under the same parent folder.
The exact parent path does not matter; only the sibling relationship is required:

```
<any-parent>\
├── sharpcap-focus-temperature\   ← this repository
│   ├── sharpcap_focuser.py
│   ├── sharpcap_focus_state.json        ← main tube state (single source of truth)
│   └── sharpcap_focus_state_guide.json  ← guide tube state (single source of truth)
└── sharpcap-focus-sequencer\      ← consumes both state JSON files
    ├── focus_sequencer.py
    ├── run_focus.bat              ← main tube entry point
    └── run_focus_guide.bat        ← guide tube entry point
```

`sharpcap_focus_state.json` and `sharpcap_focus_state_guide.json` are generated by
`sharpcap_focuser.py` and automatically refreshed by the sequencer before each
thermal correction. **Do not copy them** into the sibling repository — that would
create stale duplicates that silently drift from the real models.

See the [sharpcap-focus-sequencer](https://github.com/davidglt/sharpcap-focus-sequencer)
README for the full two-repository workflow and installation instructions.

## Installation

Clone the repository and create the virtual environment:

```bash
cd <any-parent>
git clone https://github.com/davidglt/sharpcap-focus-temperature.git
cd sharpcap-focus-temperature
python -m venv .venv
.venv\Scripts\pip install -r requirements\requirements.txt
```

Always invoke the script through the project’s own virtual environment:

```bash
.venv\Scripts\python.exe sharpcap_focuser.py
```

### Windows Execution Policy

By default, Windows may block scripts downloaded from the internet.
To allow the virtual environment activation scripts to run, set the execution
policy for the current user **once** from an elevated PowerShell prompt:

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

> **What this does:** allows locally created scripts to run, and allows
> downloaded scripts that are signed by a trusted publisher. It does **not**
> disable Windows Defender or any other security mechanism.
>
> If you prefer a narrower change, you can unblock only the specific files
> instead:
> ```powershell
> Unblock-File -Path C:\astro\sharpcap-focus-temperature\sharpcap_focuser.py
> ```

## Requirements

- Python 3.10 or newer recommended.
- `numpy`
- `matplotlib`
- `statsmodels`

Install dependencies with:

```bash
pip install -r requirements\requirements.txt
```

## Usage

Basic example — main tube (uses default SharpCap log path on Windows):

```bash
python sharpcap_focuser.py
```

Guide tube — generate a separate model for the 50ED:

```bash
python sharpcap_focuser.py --tube guide
```

Example with custom limits and target temperature (main tube):

```bash
python sharpcap_focuser.py \
  --min-position 24000 \
  --max-position 27000 \
  --x-min 24000 \
  --x-max 27000 \
  --y-min -10 \
  --y-max 40 \
  --predict-temperature 12.5
```

Example using only the last 7 calendar days and automatic axis scaling:

```bash
python sharpcap_focuser.py \
  --last-days 7 \
  --auto-axis
```

Example disabling outlier removal:

```bash
python sharpcap_focuser.py --no-remove-outliers
```

Example writing the JSON state file to a custom path:

```bash
python sharpcap_focuser.py \
  --output-state-json /path/to/sequencer/focus_state.json
```

## Command-line options

| Option | Default | Description |
|---|---|---|
| `--tube` | `main` | Tube to analyse: `main` (C8 + ASI2600MC Pro, 24 000–27 000 steps) or `guide` (50ED + ASI224MC, 315 000–365 000 steps). Selects per-tube defaults for position range and output file names. |
| `--log-path` | SharpCap logs folder | SharpCap log folder path. |
| `--output-csv` | per tube | Output CSV file path. |
| `--output-state-json` | per tube | Output JSON file with last valid autofocus reference and regression model. |
| `--min-position` | per tube | Minimum focuser position to keep. |
| `--max-position` | per tube | Maximum focuser position to keep. |
| `--x-min` | per tube | Minimum X axis limit. |
| `--x-max` | per tube | Maximum X axis limit. |
| `--y-min` | `-10` | Minimum Y axis limit (°C). |
| `--y-max` | `40` | Maximum Y axis limit (°C). |
| `--auto-axis` | off | Use automatic axis scaling instead of fixed limits. |
| `--last-days` | all history | Include only results from the last N calendar days. |
| `--predict-temperature` | none | Predict focuser position for a target temperature (°C). |
| `--no-remove-outliers` | off | Disable outlier removal. |
| `--studentized-threshold` | `3.0` | Threshold for studentized residual outlier rejection. |

## How outlier filtering works

When enabled, the script fits a first-pass linear model and computes externally
studentized residuals for each point.  Any point whose absolute studentized
residual exceeds the threshold is removed and written to the outliers CSV.

Default threshold:

```text
|t| > 3.0
```

Requires at least 5 data points to activate; if fewer are available the filter
is silently skipped.

> **Note on synthetic data and outlier filtering:** synthetic points are evaluated
> by the same studentized residual threshold as real observations. However, to
> avoid artificially compressing the model’s residual variance, synthetic points
> should include realistic scatter (σ ≈ 500–1 000 steps). A perfectly collinear
> synthetic set would make the model overly sensitive and could incorrectly flag
> valid real observations as outliers.

## SharpCap log location

Default Windows path:

```text
%USERPROFILE%\AppData\Local\SharpCap\logs
```

This is used automatically unless `--log-path` is specified.

## Typical workflow

1. Run several autofocus operations during one or more imaging sessions.
2. Execute the script against the SharpCap log folder.
   - Main tube: `python sharpcap_focuser.py`
   - Guide tube: `python sharpcap_focuser.py --tube guide`
3. Inspect the corresponding CSV and PNG files.
4. Review `k`, `TCF`, and the focus prediction for the temperature of interest.
5. The sibling [sharpcap-focus-sequencer](https://github.com/davidglt/sharpcap-focus-sequencer)
   reads the appropriate state JSON and applies temperature-based corrections
   automatically during each nightly session, refreshing the model before every
   correction cycle (live mode only; `--dry-run` uses the previously written
   state without refreshing it from logs).
6. Repeat after collecting more sessions to refine the regression.

## License

This project is licensed under the **GNU General Public License v3.0 or later**.

See the `LICENSE` file for the full license text.

## Author

**David González López-Tercero**  
Website: [https://dragonit.es](https://dragonit.es)  
Email: [davidglt@dragonit.es](mailto:davidglt@dragonit.es)
