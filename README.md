# Weer performance indicator Nederland

Collects hourly 48-hour temperature forecasts for ten large Dutch cities from Weerplaza.

## Data format

Each city has one dataframe-friendly CSV in `city_data/`. Every row is one hourly forecast snapshot. Columns are `sampled_at`, `temperature_now_c`, and `temperature_plus_1h_c` through `temperature_plus_47h_c`. All temperature values are forecasts in Celsius; there are no actual-temperature observations. `temperature_now_c` is the forecast for the current local clock hour.

The collector appends one row per city each hour and retains the rows without a time limit. The CSVs contain only the current wide forecast format; the earlier ten-minute actual-reading files were cleared.

## GitHub Actions

The workflow in `.github/workflows/collect-weather.yml` runs hourly on the repository's default branch and commits updated city CSVs with the built-in `GITHUB_TOKEN`. It requires no secrets. The first snapshot can be triggered from **Actions → Collect Dutch city temperatures → Run workflow**.

GitHub can delay scheduled runs during busy periods. Standard hosted runners are free for public repositories. Data is committed to the public repository.

## Run locally

```powershell
python -m pip install -r requirements.txt
python temperature_collector.py
```

Use `python temperature_collector.py --once` to collect one row for each city and exit.

## Source

Weerplaza city pages provide the hourly 48-hour forecast. If Weerplaza changes its page structure, the collector logs the error in the Actions run and writes a row with blank forecast cells for that city.
