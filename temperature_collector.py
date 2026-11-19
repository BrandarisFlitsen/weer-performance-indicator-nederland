"""Append current and 48-hour forecast temperatures to a CSV per city.

Install requirements with ``pip install -r requirements.txt`` and run
``python temperature_collector.py``. A sample is collected immediately and
then every ten minutes at wall-clock boundaries in Europe/Amsterdam. Forecasts
are refreshed when the previous snapshot is an hour old (or absent).
"""

from __future__ import annotations

import argparse
import logging
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from bs4 import BeautifulSoup


PROJECT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = PROJECT_DIR / "city_data"
TIMEZONE = ZoneInfo("Europe/Amsterdam")

# Weerplaza location pages for the ten largest Dutch cities by population.
# The display name is used to locate the current-conditions heading on each page.
CITIES = {
    "Amsterdam": ("https://www.weerplaza.nl/nederland/amsterdam/5585/", "Amsterdam"),
    "Rotterdam": ("https://www.weerplaza.nl/nederland/rotterdam/15020/", "Rotterdam"),
    "The Hague": ("https://www.weerplaza.nl/nederland/den%20haag/8584/", "Den Haag"),
    "Utrecht": ("https://www.weerplaza.nl/nederland/utrecht/19344/", "Utrecht"),
    "Eindhoven": ("https://www.weerplaza.nl/nederland/eindhoven/2718/", "Eindhoven"),
    "Groningen": ("https://www.weerplaza.nl/nederland/groningen/8274/", "Groningen"),
    "Tilburg": ("https://www.weerplaza.nl/nederland/tilburg/18904/", "Tilburg"),
    "Almere": ("https://www.weerplaza.nl/nederland/almere/5445/", "Almere"),
    "Breda": ("https://www.weerplaza.nl/nederland/breda/2754945/", "Breda"),
    "Nijmegen": ("https://www.weerplaza.nl/nederland/nijmegen/14635/", "Nijmegen"),
}

DAY_NUMBERS = {"ma": 0, "di": 1, "wo": 2, "do": 3, "vr": 4, "za": 5, "zo": 6}
DAY_RE = re.compile(r"^(ma|di|wo|do|vr|za|zo)$", re.IGNORECASE)
TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
TEMP_RE = re.compile(r"^(-?\d+(?:[.,]\d+)?)\s*°\s*C?$", re.IGNORECASE)
FORECAST_TEMP_RE = re.compile(r"^(-?\d+(?:[.,]\d+)?)\s*°\s*C$", re.IGNORECASE)
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; TemperatureCollector/1.0)"}
CSV_COLUMNS = ["sampled_at", "record_type", "valid_at", "temperature_c"]

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def parse_temperature(lines: list[str], heading: re.Pattern[str], city: str) -> float:
    for index, line in enumerate(lines):
        if heading.match(line):
            for candidate in lines[index + 1 : index + 8]:
                match = TEMP_RE.match(candidate)
                if match:
                    return float(match.group(1).replace(",", "."))
                if candidate.startswith(("Neerslag:", "Wind:", "Druk:", "Vochtigheid:")):
                    break
    raise ValueError(f"Could not find current temperature for {city}")


def resolve_forecast_time(day: str, hour: int, minute: int, now: datetime) -> datetime:
    days_ahead = (DAY_NUMBERS[day.lower()] - now.weekday()) % 7
    forecast_date: date = now.date() + timedelta(days=days_ahead)
    forecast_time = datetime.combine(forecast_date, datetime.min.time(), tzinfo=TIMEZONE).replace(
        hour=hour, minute=minute
    )
    if forecast_time < now - timedelta(minutes=59):
        forecast_time += timedelta(days=7)
    return forecast_time


def parse_hourly_forecast(lines: list[str], now: datetime) -> list[tuple[datetime, float]]:
    """Parse the duplicated weekday/time and temperature entries in Weerplaza's 48h block."""
    try:
        start = next(i for i, line in enumerate(lines) if line == "Het weer de komende 48 uur") + 1
    except StopIteration as exc:
        raise ValueError("Weerplaza 48-hour forecast section not found") from exc

    section_end = next(
        (i for i in range(start, len(lines)) if lines[i].startswith("Weersverwachting ")),
        len(lines),
    )
    section = lines[start:section_end]
    forecasts: dict[datetime, float] = {}
    for i in range(len(section) - 5):
        day = section[i].lower()
        if not DAY_RE.match(day) or not TIME_RE.match(section[i + 1]):
            continue
        # The page repeats the weekday and time before showing the temperature.
        if section[i : i + 2] != section[i + 2 : i + 4]:
            continue
        time_match = TIME_RE.match(section[i + 1])
        temp_match = FORECAST_TEMP_RE.match(section[i + 4])
        if not time_match or not temp_match:
            continue
        valid_at = resolve_forecast_time(
            day, int(time_match.group(1)), int(time_match.group(2)), now
        )
        if now < valid_at <= now + timedelta(hours=48):
            forecasts[valid_at] = float(temp_match.group(1).replace(",", "."))

    if not forecasts:
        raise ValueError("No hourly forecast temperatures could be parsed")
    return sorted(forecasts.items())


def fetch_city_data(
    city: str,
    url: str,
    display_name: str,
    now: datetime,
    include_forecast: bool,
) -> list[dict[str, object]]:
    response = requests.get(url, headers=HEADERS, timeout=20)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    lines = [line.strip() for line in soup.get_text("\n").splitlines() if line.strip()]

    heading = re.compile(rf"^Het weer nu in {re.escape(display_name)}(?:\s|$)", re.IGNORECASE)
    current_temp = parse_temperature(lines, heading, city)
    sampled_at = now.isoformat(timespec="seconds")
    records: list[dict[str, object]] = [
        {
            "sampled_at": sampled_at,
            "record_type": "actual",
            "valid_at": sampled_at,
            "temperature_c": current_temp,
        }
    ]
    if include_forecast:
        try:
            forecast = parse_hourly_forecast(lines, now)
            records.extend(
                {
                    "sampled_at": sampled_at,
                    "record_type": "forecast",
                    "valid_at": valid_at.isoformat(timespec="seconds"),
                    "temperature_c": temperature,
                }
                for valid_at, temperature in forecast
            )
        except Exception as exc:
            # A changed forecast layout should not discard a valid current reading.
            logging.warning("%s: forecast parse failed; current reading will still be saved: %s", city, exc)
    return records


def city_file(city: str) -> Path:
    slug = re.sub(r"[^a-z0-9]+", "_", city.lower()).strip("_")
    return OUTPUT_DIR / f"{slug}.csv"


def forecast_is_due(path: Path, now: datetime) -> bool:
    """Refresh a city's forecast at least hourly, recovering automatically if overdue."""
    if not path.exists():
        return True
    try:
        history = pd.read_csv(path, usecols=["sampled_at", "record_type"])
        forecasts = history.loc[history["record_type"] == "forecast", "sampled_at"]
        if forecasts.empty:
            return True
        latest = pd.to_datetime(forecasts, utc=True).max().to_pydatetime()
        return now.astimezone(ZoneInfo("UTC")) - latest >= timedelta(minutes=55)
    except Exception as exc:
        logging.warning("Could not read forecast history in %s; refreshing it: %s", path, exc)
        return True


def collect_once() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(TIMEZONE)
    for city, (url, display_name) in CITIES.items():
        path = city_file(city)
        try:
            include_forecast = forecast_is_due(path, now)
            records = fetch_city_data(city, url, display_name, now, include_forecast)
            frame = pd.DataFrame(records, columns=CSV_COLUMNS)
            frame.to_csv(path, mode="a", header=not path.exists(), index=False)
            forecast_count = len(records) - 1
            logging.info(
                "%s: saved current temperature%s to %s",
                city,
                f" and {forecast_count} forecast hours" if include_forecast else "",
                path,
            )
        except Exception as exc:
            logging.exception("%s: collection failed: %s", city, exc)


def seconds_until_next_interval(now: datetime) -> float:
    next_minute = ((now.minute // 10) + 1) * 10
    next_time = now.replace(second=0, microsecond=0)
    if next_minute == 60:
        next_time = next_time.replace(minute=0) + timedelta(hours=1)
    else:
        next_time = next_time.replace(minute=next_minute)
    return max(0.0, (next_time - now).total_seconds())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--once",
        action="store_true",
        help="collect one sample and exit (used by GitHub Actions)",
    )
    args = parser.parse_args()
    logging.info("Starting Weerplaza collection; per-city files will be written to %s", OUTPUT_DIR)
    try:
        while True:
            collect_once()
            if args.once:
                return
            time.sleep(seconds_until_next_interval(datetime.now(TIMEZONE)))
    except KeyboardInterrupt:
        logging.info("Stopped by user")


if __name__ == "__main__":
    main()
