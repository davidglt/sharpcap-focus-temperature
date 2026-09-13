# SharpCap Autofocus Log Extractor and Temperature Regression Plotter

Python script for extracting autofocus results from SharpCap logs, filtering and
cleaning the data, and generating a CSV export, a regression chart that relates
focuser position to temperature, and a JSON state file with the latest autofocus
reference and the fitted thermal model.

The script supports two optical tubes through `--tube {main,guide}` and can load
a persistent, local focus configuration for each tube from
`focus_config.properties`.

| Tube | Optical train | Focuser | Default focus center | Default interval | State JSON |
|---|---|---|---:|---:|---|
| `main` (default) | Celestron C8 + f/6.3 focal reducer + ZWO EFW7 + ZWO ASI2600MC Pro | ZWO EAF | 18,706 steps | 17,206–20,206 | `sharpcap_focus_state.json` |
| `guide` | Sky-Watcher 50ED + UV/IR-cut filter + ZWO ASI224MC | ZWO EAF | 391,982 steps | 366,982–416,982 | `sharpcap_focus_state_guide.json` |

> **Guide tube note:** the 50ED EAF (`ASCOM.EAF_2.Focuser`) can have a much
> larger mechanical range than the analysis interval. The script filters
> autofocus results using the configured focus interval; the EAF hardware limit
> remains independent. The guide profile refers to the installed optical train:
> Sky-Watcher 50ED + UV/IR-cut filter + ZWO ASI224MC. If this optical train
> changes, refocus, determine a new stable centre, record its temperature, and
> update the `[guide]` configuration before mixing new data with earlier
> sessions.

## What it does

- Reads SharpCap `Log_*.log` files.
- Extracts autofocus results with timestamp, temperature and best-focus position.
- Filters observations by a per-tube focus interval.
- Reads persistent centres, interval widths, reference temperatures and
  estimated TCF values from `focus_config.properties`.
- Supports temporary `--focus-center` and `--focus-range` command-line overrides.
- Filters data by the last N natural calendar days.
- Optionally removes outliers using externally studentized residuals.
- Fits a linear regression between temperature and focuser position.
- Calculates the temperature compensation factor (TCF) from the fitted
  regression, in steps/°C.
- Predicts focuser position for a target temperature.
- Generates synthetic focus/temperature samples for a selected tube.
- Exports cleaned results to CSV and removed outliers to a separate CSV.
- Exports the latest valid autofocus reference and fitted thermal model to JSON.
- Generates a chart with regression, prediction marker, legend and summary tables.

## Focus configuration

### Persistent per-tube settings

Copy the tracked template to create your local configuration:

```powershell
Copy-Item focus_config.properties.example focus_config.properties
```

`focus_config.properties` is intentionally excluded by `.gitignore`, so each
observatory can maintain its own optical-train references without committing
them.

Example:

```ini
; Local per-tube focus configuration.
; This file is intentionally ignored by Git.
;
; focus_center is the EAF position in steps at temperature_center.
; focus_range is the allowed full focuser interval width in EAF steps.
; temperature_center is the focuser temperature in degrees Celsius at which
; focus_center is valid. It is the reference point used to generate synthetic
; focus-temperature data.
; estimated_tcf is the estimated temperature compensation factor in EAF
; steps per degree Celsius. It is used for synthetic data generation unless
; --tcf is supplied as an override.

[main]
; Optical train: Celestron C8 + f/6.3 focal reducer + ZWO EFW7
; + ZWO ASI2600MC Pro.
; Focuser: ZWO EAF.
focus_center = 18706
focus_range = 3000
temperature_center = 19.43
estimated_tcf = -60.00

[guide]
; Optical train: Sky-Watcher 50ED + UV/IR-cut filter + ZWO ASI224MC.
; Focuser: ZWO EAF.
focus_center = 391982
focus_range = 50000
temperature_center = 18.70
estimated_tcf = -977.18
```

For each tube, the script derives the accepted focus interval as:

\[
P_{\min}=P_{\mathrm{center}}-\frac{W}{2}
\]

\[
P_{\max}=P_{\mathrm{center}}+\frac{W}{2}
\]

where \(P_{\mathrm{center}}\) is `focus_center` and \(W\) is `focus_range`.
`focus_range` must be a positive, even integer, so the integer interval is
centred exactly on `focus_center`.

`temperature_center` is the focuser temperature in °C at which
`focus_center` is valid. Write it with two decimal places, for example
`19.43`. It anchors the synthetic focus-temperature relation generated with
`--generate-synthetic-data`.

`estimated_tcf` is an optional, per-tube initial temperature compensation
estimate in EAF steps/°C. It is used as the default by
`--generate-synthetic-data` when `--tcf` is not supplied. It does not replace
the `model_tcf` derived from real autofocus observations and written to the
state JSON.

For each generation run, the effective TCF is resolved in this order:

1. `--tcf`, when supplied on the command line.
2. `estimated_tcf` from the selected `[main]` or `[guide]` configuration
   section.
3. An error if neither is available.

> **TCF sign:** for the installed C8 and 50ED optical trains, the expected TCF
> is normally negative: focus moves inward, to fewer EAF steps, as temperature
> rises.

For the supplied main-tube values:

```text
focus_center       = 18706
focus_range        = 3000
temperature_center = 19.43
estimated_tcf      = -60.00
```

the calculated focus range is:

```text
17206–20206
```

### Why the configuration is external

The EAF step value is a reference coordinate, not an absolute optical measure.
Changing a reducer, spacing, filter, camera, focuser coupling or mirror
position can displace the measured best-focus position substantially. These
changes belong in local configuration, not as code edits.

For example, a C8 configuration whose focus changes from about 25,500 to
18,706 after an optical-train modification has shifted by −6,794 steps. With
the same 3,000-step interval width, the useful range moves from `24,000–27,000`
to `17,206–20,206`. Establish the new best focus, record the focuser
temperature as `temperature_center`, then update the affected local section.

### Configuration precedence

The script resolves focus limits using this order, from highest to lowest
priority:

1. `--focus-center` with optional `--focus-range`, for a one-run override.
2. The selected `[main]` or `[guide]` section in `focus_config.properties`.
3. Internal fallback defaults in `TUBE_DEFAULTS`.

`--focus-center` and `--focus-range` are deliberately temporary: they do not
rewrite `focus_config.properties`. After a new value has been verified across
several autofocus runs, edit the corresponding local configuration section to
make it persistent.

## Main outputs

### Main tube (`--tube main`, default)

| File | Description |
|---|---|
| `sharpcap_data_focus.csv` | Filtered real autofocus results |
| `sharpcap_removed_outliers.csv` | Removed outliers with studentized-residual diagnostics |
| `sharpcap_focus_temperature.png` | Plot with regression line, prediction marker and summary tables |
| `sharpcap_focus_state.json` | Last valid autofocus reference plus fitted thermal model |
| `sharpcap_synthetic_data_focus.csv` | Optional generated synthetic focus-temperature data |

### Guide tube (`--tube guide`)

| File | Description |
|---|---|
| `sharpcap_data_focus_guide.csv` | Filtered real autofocus results |
| `sharpcap_removed_outliers_guide.csv` | Removed outliers with studentized-residual diagnostics |
| `sharpcap_focus_temperature_guide.png` | Plot with regression line, prediction marker and summary tables |
| `sharpcap_focus_state_guide.json` | Last valid autofocus reference plus fitted thermal model |
| `sharpcap_synthetic_data_focus_guide.csv` | Optional generated synthetic focus-temperature data |

## Regression model

The analysis fits focuser position as a linear function of temperature:

```text
P = m * T + c
```

where:

- `P` is focuser position in EAF steps.
- `T` is focuser temperature in °C.
- `m` is the temperature compensation factor, TCF, in steps/°C.
- `c` is the position intercept in steps.

For compatibility with the state JSON, the script also stores the inverse
relation:

```text
T = k * P + b
```

where:

- `k = 1 / TCF` is `model_inv_tcf`, in °C/step.
- `b` is `model_intercept_c`, in °C.

The generated JSON field `model_tcf` is the measured TCF from the fitted
autofocus data. It must not be confused with `estimated_tcf`, which is the
local configuration estimate used to seed synthetic-data generation.

## Synthetic data

When real autofocus observations are scarce—such as during a new season or
after changing an optical train—the regression can be provisionally anchored
with generated synthetic focus/temperature pairs.

Synthetic samples act as a temporary weak prior: they participate in the same
regression and studentized-residual process as measured data, then naturally
lose influence as genuine observations accumulate.

> **Important:** synthetic samples do not replace real autofocus observations.
> Remove or regenerate them after collecting enough measurements across the
> seasonal temperature range applicable to the installed optical train.

### Generated files

The generator derives the synthetic CSV path from the selected tube:

| Tube | Synthetic CSV |
|---|---|
| `main` | `sharpcap_synthetic_data_focus.csv` |
| `guide` | `sharpcap_synthetic_data_focus_guide.csv` |

The script detects the relevant synthetic CSV automatically during normal
analysis.

### Generation model

For each synthetic sample, focuser position is calculated as:

\[
P_i =
P_{\mathrm{center}} +
\mathrm{TCF}
\left(T_i-T_{\mathrm{center}}\right) +
\varepsilon_i
\]

where:

- \(P_i\) is the generated focuser position in steps.
- \(P_{\mathrm{center}}\) is `focus_center`.
- \(T_i\) is the generated focuser temperature.
- \(T_{\mathrm{center}}\) is `temperature_center`.
- TCF is the effective value in steps/°C: `--tcf` when supplied, otherwise
  `estimated_tcf` from the selected tube configuration.
- \(\varepsilon_i\) is Student's t-distributed random noise.

The generator distributes samples over the preceding 365 days and models a
seasonal temperature curve with a summer maximum. It also adds small random
temperature variation. Focuser-position noise follows a normalized Student's t
distribution: `--student-dof` controls how frequently more extreme residuals
occur, while `--noise-stddev` sets their target standard deviation in steps.

Every generated focuser position is constrained to the configured focus
interval. The script reports how many samples were clipped; a substantial
clipping fraction indicates that the selected TCF, temperature range or
`focus_range` should be reconsidered.

### Generation examples

The examples below use `estimated_tcf` from `focus_config.properties`.
Use `--tcf` only when a temporary value is required for a test or
recalibration.

### Main tube: `--tube main`

The `main` profile represents the Celestron C8 optical train with f/6.3 focal
reducer, ZWO EFW7 and ZWO ASI2600MC Pro. Its local configuration supplies the
reference focus, temperature and `estimated_tcf`.

Preview main-tube generation without writing a CSV:

```powershell
.venv\Scripts\python.exe sharpcap_focuser.py `
  --tube main `
  --generate-synthetic-data `
  --samples 12 `
  --dry-run
```

Generate or replace the main-tube synthetic CSV:

```powershell
.venv\Scripts\python.exe sharpcap_focuser.py `
  --tube main `
  --generate-synthetic-data `
  --samples 12 `
  --overwrite
```

Representative main-tube data:

| DateTime | TemperatureC | FocuserSteps |
|---|---:|---:|
| 2025-09-13 12:53:34 | 6.36 | 19493 |
| 2025-10-16 17:15:23 | 10.63 | 19248 |
| 2025-11-18 21:37:12 | 16.94 | 18857 |
| 2025-12-22 01:59:01 | 20.37 | 18650 |
| 2026-01-24 06:20:50 | 24.71 | 18376 |
| 2026-02-26 10:42:39 | 31.92 | 17975 |
| 2026-03-31 15:04:28 | 33.67 | 17845 |
| 2026-05-03 19:26:17 | 29.81 | 18084 |
| 2026-06-05 23:48:06 | 19.30 | 18715 |
| 2026-07-09 04:09:55 | 12.56 | 19106 |
| 2026-08-11 08:31:44 | 8.43 | 19361 |
| 2026-09-13 12:53:34 | 5.58 | 19540 |

The table was generated with the `main` profile. Its recovered TCF is
approximately `-62.2 steps/°C`, consistent with the configured
`estimated_tcf = -60.00`. The small difference comes from the generated
Student's t-distributed position noise and the limited sample count.

### Guide tube: `--tube guide`

The `guide` profile represents the Sky-Watcher 50ED optical train with its
UV/IR-cut filter and ZWO ASI224MC. Its `estimated_tcf` is a starting value for
that installed configuration and should be recalibrated after an optical change.

Preview guide-tube generation without writing a CSV:

```powershell
.venv\Scripts\python.exe sharpcap_focuser.py `
  --tube guide `
  --generate-synthetic-data `
  --samples 12 `
  --noise-stddev 500 `
  --dry-run
```

Generate or replace the guide-tube synthetic CSV:

```powershell
.venv\Scripts\python.exe sharpcap_focuser.py `
  --tube guide `
  --generate-synthetic-data `
  --samples 12 `
  --noise-stddev 500 `
  --overwrite
```

Representative guide-tube data:

| DateTime | TemperatureC | FocuserSteps |
|---|---:|---:|
| 2025-09-13 13:15:00 | 4.71 | 405638 |
| 2025-10-16 17:36:49 | 6.49 | 403919 |
| 2025-11-18 21:58:38 | 15.11 | 395487 |
| 2025-12-22 02:20:27 | 21.52 | 389224 |
| 2026-01-24 06:42:16 | 26.78 | 384082 |
| 2026-02-26 11:04:05 | 30.29 | 380639 |
| 2026-03-31 15:25:54 | 32.53 | 378469 |
| 2026-05-03 19:47:43 | 27.87 | 383034 |
| 2026-06-06 00:09:32 | 20.87 | 389868 |
| 2026-07-09 04:31:21 | 12.97 | 397578 |
| 2026-08-11 08:53:10 | 6.51 | 403856 |
| 2026-09-13 13:15:00 | 3.64 | 406704 |

The recovered guide relation is approximately:

```text
FocuserSteps = -976.87 * TemperatureC + 410245.58
```

The recovered TCF is approximately `-976.87 steps/°C`, consistent with
`estimated_tcf = -977.18`. The small residual variation has an RMS of about
13 steps. All sample positions are inside the configured interval
`366982–416982`, so this example has no focus-range clipping.

The example tables are real generator outputs. Later runs can differ because
the generator uses seasonal temperature sampling and Student's t-distributed
focus-position noise. Use `--dry-run` to inspect the active configuration and
diagnostics before replacing an existing CSV.

If the target synthetic CSV already exists, generation stops to protect it.
Use `--overwrite` explicitly to replace it:

```powershell
.venv\Scripts\python.exe sharpcap_focuser.py `
  --tube main `
  --generate-synthetic-data `
  --overwrite
```

### Generator options

| Option | Default | Description |
|---|---:|---|
| `--generate-synthetic-data` | Off | Generate synthetic data for the selected tube and exit |
| `--tcf TCF` | None | Temporary TCF override in steps/°C for synthetic generation; overrides the selected tube's `estimated_tcf` |
| `--samples N` | `12` | Number of synthetic samples to generate |
| `--student-dof N` | `8` | Student's t degrees of freedom; lower values create heavier tails and more extreme synthetic deviations; must be greater than 2 |
| `--noise-stddev STEPS` | `12.0` | Target standard deviation of synthetic position noise, in focuser steps |
| `--dry-run` | Off | Preview synthetic generation and diagnostics without writing a CSV |
| `--overwrite` | Off | Replace an existing synthetic CSV |

The console summary displays the effective TCF and its source, the recovered
TCF fitted from generated points, their difference, position-temperature
correlation and the number of samples clipped to the focus interval.

### CSV format

Synthetic CSV files use the same three-column format expected by the analysis
loader:

```csv
DateTime,TemperatureC,FocuserSteps
```

Temperatures are written to two decimal places. Synthetic and real observations
are merged before regression; synthetic points are plotted in green and are
excluded from the generated cleaned-real-data CSV and JSON autofocus reference.
Expelled synthetic points are included in the removed-outliers CSV with
`Synthetic = yes`.

> **After an optical change:** do not blindly reuse a synthetic CSV from the
> former train. Establish a new `focus_center` and `temperature_center`, set a
> suitable interval, update `estimated_tcf` if required, and generate new
> samples. Replace the synthetic CSV after sufficient genuine autofocus
> observations have been collected.

## JSON state file

After each successful normal analysis run, the script writes the per-tube state
JSON. It is the single source of truth for the latest reference and fitted
thermal model:

```json
{
  "timestamp_ref": "2026-08-24 23:11:32",
  "temp_ref": 18.4,
  "focus_ref": 18700,
  "last_temp_applied": 18.4,
  "last_focus_applied": 18700,
  "model_tcf": -61.59,
  "model_inv_tcf": -0.016237,
  "model_intercept_c": 889.541
}
```

| Field | Description |
|---|---|
| `timestamp_ref` | Timestamp of the last clean autofocus result used as reference |
| `temp_ref` | Focuser temperature at the reference point in °C |
| `focus_ref` | Focuser position at the reference point in steps |
| `last_temp_applied` | Temperature at which the last correction was applied |
| `last_focus_applied` | Focuser position of the last applied correction |
| `model_tcf` | Measured TCF, in steps/°C |
| `model_inv_tcf` | Inverse TCF, in °C/step |
| `model_intercept_c` | Regression intercept in °C for the inverse relation |

`last_temp_applied` and `last_focus_applied` are separate from
`temp_ref`/`focus_ref` so the sequencer can maintain its live correction state
without overwriting the reference measurement.

## Sister repository

This repository works with
[sharpcap-focus-sequencer](https://github.com/davidglt/sharpcap-focus-sequencer)
as two sibling repositories:

```text
<any-parent>\
├── sharpcap-focus-temperature\
│   ├── sharpcap_focuser.py
│   ├── focus_config.properties.example
│   ├── focus_config.properties                 ← local, ignored by Git
│   ├── sharpcap_synthetic_data_focus.csv       ← local, ignored by Git
│   ├── sharpcap_focus_state.json
│   └── sharpcap_focus_state_guide.json
└── sharpcap-focus-sequencer\
    ├── focus_sequencer.py
    ├── run_focus.bat
    └── run_focus_guide.bat
```

The state JSON files are generated by `sharpcap_focuser.py` and refreshed by the
sequencer before thermal corrections. Do not copy them into the sibling
repository: that would create stale duplicate state.

## Installation

```powershell
cd <any-parent>
git clone [https://github.com/davidglt/sharpcap-focus-temperature.git](https://github.com/davidglt/sharpcap-focus-temperature.git)
cd sharpcap-focus-temperature
python -m venv .venv
.venv\Scripts\pip.exe install -r requirements\requirements.txt
Copy-Item focus_config.properties.example focus_config.properties
```

Always invoke the script through its local virtual environment:

```powershell
.venv\Scripts\python.exe sharpcap_focuser.py
```

### Windows execution policy

If Windows blocks activation scripts downloaded from the internet, set the
current-user policy once from an elevated PowerShell prompt:

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

Or unblock only a specific local script:

```powershell
Unblock-File -Path C:\astro\sharpcap-focus-temperature\sharpcap_focuser.py
```

## Requirements

- Python 3.10 or newer.
- `numpy`
- `matplotlib`
- `statsmodels`

Install dependencies:

```powershell
pip install -r requirements\requirements.txt
```

## Usage

### Standard per-tube runs

Main tube, using `[main]` from `focus_config.properties`:

```powershell
python sharpcap_focuser.py --tube main
```

Guide tube, using `[guide]` from `focus_config.properties`:

```powershell
python sharpcap_focuser.py --tube guide
```

### Temporary recentring

Test a new main-tube focus centre while retaining its configured interval width:

```powershell
python sharpcap_focuser.py --tube main --focus-center 19000
```

Test a new guide-tube reference and temporary 20,000-step interval:

```powershell
python sharpcap_focuser.py `
  --tube guide `
  --focus-center 350000 `
  --focus-range 20000
```

The latter analyses `340000–360000` for that run only.

### Chart axis overrides

Use explicit chart limits for a one-off historical visualization without
changing the focus filter interval:

```powershell
python sharpcap_focuser.py `
  --tube main `
  --focus-center 18706 `
  --focus-range 3000 `
  --x-min 17206 `
  --x-max 20206 `
  --y-min -10 `
  --y-max 40 `
  --predict-temperature 12.5
```

`--x-min` and `--x-max` must be supplied together. They affect the chart only;
the autofocus filter always uses the active `focus_center` and `focus_range`.

### Other examples

```powershell
python sharpcap_focuser.py --tube main --last-days 7 --auto-axis

python sharpcap_focuser.py --tube guide --no-remove-outliers

python sharpcap_focuser.py `
  --tube main `
  --output-state-json C:\astro\sharpcap-focus-sequencer\focus_state.json
```

## Command-line options

| Option | Default | Description |
|---|---|---|
| `--tube` | `main` | Tube to analyse: `main` or `guide` |
| `--config` | `focus_config.properties` beside the script | Local `.properties` configuration file |
| `--log-path` | SharpCap log folder | SharpCap log folder path |
| `--output-csv` | Per tube | Output CSV file path |
| `--output-state-json` | Per tube | Output JSON state path |
| `--focus-center` | None | Temporary focus centre; uses the configured range if `--focus-range` is omitted |
| `--focus-range` | None | Temporary total interval width; requires `--focus-center` |
| `--x-min` | Calculated interval minimum | Manual X-axis lower limit; requires `--x-max`; does not change data filtering |
| `--x-max` | Calculated interval maximum | Manual X-axis upper limit; requires `--x-min`; does not change data filtering |
| `--y-min` | `-10` | Y-axis lower limit in °C |
| `--y-max` | `40` | Y-axis upper limit in °C |
| `--auto-axis` | Off | Use automatic axis scaling |
| `--last-days` | All history | Include only the last N calendar days |
| `--predict-temperature` | None | Predict focus position for a target temperature |
| `--no-remove-outliers` | Off | Disable outlier removal |
| `--studentized-threshold` | `3.0` | Absolute studentized-residual rejection threshold |
| `--generate-synthetic-data` | Off | Generate synthetic data for the selected tube and exit |
| `--tcf` | None | Temporary TCF override in steps/°C for synthetic generation; overrides the selected tube's `estimated_tcf` |
| `--samples` | `12` | Number of synthetic samples |
| `--student-dof` | `8` | Student's t degrees of freedom for synthetic position noise |
| `--noise-stddev` | `12.0` | Synthetic focus-position noise target standard deviation in steps |
| `--dry-run` | Off | Preview synthetic generation without writing a CSV |
| `--overwrite` | Off | Replace an existing synthetic CSV |

## Outlier filtering

With outlier filtering enabled, the script fits a first-pass linear model and
calculates externally studentized residuals. Any point satisfying:

```text
|t| > 3.0
```

is removed and written to the outliers CSV. At least five observations are
required; with fewer, filtering is skipped.

Synthetic points use the same test. The generator intentionally applies
Student's t-distributed focus-position variation, making occasional values
farther from the ideal TCF line statistically plausible. Lower
`--student-dof` values create heavier tails and offer a useful way to exercise
the outlier rejection path.

## SharpCap log location

Default Windows path:

```text
%USERPROFILE%\AppData\Local\SharpCap\logs
```

Use `--log-path` to select another folder.

## Typical workflow

1. Set `focus_center`, `focus_range`, `temperature_center` and
   `estimated_tcf` for each installed optical train in
   `focus_config.properties`.

2. After a new train configuration, preview and then generate a provisional
   synthetic dataset:

   ```powershell
   python sharpcap_focuser.py --tube main --generate-synthetic-data --dry-run
   python sharpcap_focuser.py --tube main --generate-synthetic-data --overwrite
   ```

3. Run several real autofocus operations during one or more sessions.

4. Generate or refresh the real-data model:

   ```powershell
   python sharpcap_focuser.py --tube main
   python sharpcap_focuser.py --tube guide
   ```

5. Inspect the per-tube CSV, chart, state JSON and any removed-outliers CSV.

6. Review TCF and predicted focus at the temperature of interest.

7. Let the sibling sequencer refresh and use the appropriate state JSON during
   nightly operation.

8. After enough valid real autofocus observations cover the normal seasonal
   range, remove the corresponding synthetic CSV.

9. After changing camera, reducer, filter, spacing or focuser mechanics,
   establish a new focus reference, update the appropriate `.properties`
   section—including `estimated_tcf` when necessary—and generate a fresh
   temporary dataset if required.

## License

This project is licensed under the GNU General Public License v3.0 or later.
See `LICENSE.txt` for the full text.

## Author

David González López-Tercero  
Website: [dragonit.es](https://dragonit.es)  
Email: [davidglt@dragonit.es](mailto:davidglt@dragonit.es)