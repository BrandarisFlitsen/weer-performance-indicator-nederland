# Weer performance indicator Nederland

Collects current temperatures and hourly 48-hour temperature forecasts for ten large Dutch cities from Weerplaza.

## Data

GitHub Actions runs the collector every ten minutes. Each city has a dataframe-friendly CSV in `city_data/` with these columns:

- `sampled_at`: when Weerplaza data was collected, in Europe/Amsterdam time
- `record_type`: `actual` for current conditions or `forecast` for a forecast value
- `valid_at`: the time the value applies to, in Europe/Amsterdam time
- `temperature_c`: temperature in Celsius

Current temperatures are sampled every ten minutes. A city's forecast is refreshed about once an hour and includes hourly values up to 48 hours ahead. Data is appended and retained; the collector does not delete older rows.

## GitHub setup

The workflow is in `.github/workflows/collect-weather.yml`. It runs on the repository's default branch and commits changed city CSVs using the built-in `GITHUB_TOKEN`; no secrets are required. The first collection can be started from **Actions → Collect Dutch city temperatures → Run workflow**. Scheduled runs start after the workflow is present on the default branch.

GitHub may delay or drop scheduled runs during high load. A public repository using standard hosted runners has free runner usage. Private repositories use the account's included Actions minutes; at ten-minute intervals, four weeks is about 4,032 runs and can exceed the free quota. GitHub's schedule documentation also notes that public repositories with no repository activity for 60 days have scheduled workflows automatically disabled.

## Run locally

```powershell
python -m pip install -r requirements.txt
python temperature_collector.py
```

Use `python temperature_collector.py --once` to collect one sample and exit.

## Source

Weerplaza city pages provide current observations and the upcoming 48-hour forecast. This project reads those displayed values; if Weerplaza changes its page structure, collection logs a parse error in the Actions run log.
