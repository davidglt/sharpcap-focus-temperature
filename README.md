# SharpCap Autofocus Log Extractor and Temperature Regression Plotter

Python script for extracting autofocus results from SharpCap logs, filtering and cleaning the data, and generating a CSV export plus a regression chart that relates focuser position to temperature.

## What it does

- Reads SharpCap `Log_*.log` files.
- Extracts autofocus results with timestamp, temperature, and best focus position.
- Filters data by focuser step range.
- Filters data by the last N natural calendar days.
- Optionally removes outliers using externally studentized residuals.
- Fits a linear regression between temperature and focuser position.
- Calculates the inverse slope as **TCF** (temperature compensation factor).
- Predicts focuser position for a target temperature.
- Exports cleaned results to CSV.
- Exports removed outliers to a separate CSV.
- Generates a chart with regression, prediction marker, legend, and summary tables.

## Main outputs

The script generates these files:

- `sharpcap_final_focus.csv` — filtered autofocus results.
- `sharpcap_removed_outliers.csv` — removed outliers with diagnostics.
- `sharpcap_focus_temperature.png` — plot with regression and summary tables.

## Regression model

The chart uses the symbolic linear model:

```text
T = k·s + b
```

Where:

- `T` = Temperature (°C)
- `s` = Focuser Steps
- `k` = slope in °C/step
- `TCF = 1/k` = temperature compensation factor in step/°C
- `b` = intercept in °C

## Requirements

- Python 3.10 or newer recommended.
- `numpy`
- `matplotlib`
- `statsmodels`

Install dependencies with:

```bash
pip install numpy matplotlib statsmodels
```

## Usage

Basic example:

```bash
python sharpcap_focus_analysis.py
```

Example with custom limits and target temperature:

```bash
python sharpcap_focus_analysis.py \
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
python sharpcap_focus_analysis.py \
  --last-days 7 \
  --auto-axis
```

Example disabling outlier removal:

```bash
python sharpcap_focus_analysis.py --no-remove-outliers
```

## Command-line options

| Option | Description |
|---|---|
| `--log-path` | SharpCap log folder path. |
| `--output-csv` | Output CSV file path. |
| `--min-position` | Minimum focuser position to keep. |
| `--max-position` | Maximum focuser position to keep. |
| `--x-min` | Minimum X axis limit. |
| `--x-max` | Maximum X axis limit. |
| `--y-min` | Minimum Y axis limit. |
| `--y-max` | Maximum Y axis limit. |
| `--auto-axis` | Use automatic axis scaling instead of fixed limits. |
| `--last-days` | Include only results from the last N calendar days. |
| `--predict-temperature` | Predict focuser position for a target temperature. |
| `--no-remove-outliers` | Disable outlier removal. |
| `--studentized-threshold` | Threshold for studentized residual outlier rejection. |

## How outlier filtering works

When enabled, the script fits a first-pass linear model and computes externally studentized residuals for each point. Any point with an absolute studentized residual above the selected threshold is removed and written to the outliers CSV.

Default threshold:

```text
|t| > 3.0
```

## SharpCap log location

Default Windows path:

```text
%USERPROFILE%\AppData\Local\SharpCap\logs
```

This is used automatically unless `--log-path` is specified.

## Typical workflow

1. Run several autofocus operations during one or more sessions.
2. Execute the script against the SharpCap log folder.
3. Inspect the CSV and the generated plot.
4. Review `k`, `TCF`, and the focus prediction for the temperature of interest.
5. Repeat after collecting more sessions to improve the regression.

## License

This project is licensed under the **GNU General Public License v3.0 or later**.

See the `LICENSE` file for the full license text.

## Author

**David González López-Tercero**  
Website: [https://dragonit.es](https://dragonit.es)  
Email: [davidglt@dragonit.es](mailto:davidglt@dragonit.es)
