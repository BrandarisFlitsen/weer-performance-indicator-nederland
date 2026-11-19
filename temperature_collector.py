"""Write one 48-hour forecast snapshot per city to a dataframe-friendly CSV.

Run ``python temperature_collector.py --once`` for one snapshot, or omit
``--once`` to collect hourly. Values are forecasts only, in Celsius.
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
HORIZON_HOURS = 48

# Weerplaza location pages for the ten largest Dutch cities by population.
CITIES = {
    "Amsterdam": "https://www.weerplaza.nl/nederland/amsterdam/5585/",
    "Rotterdam": "https://www.weerplaza.nl/nederland/rotterdam/15020/",
    "The Hague": "https://www.weerplaza.nl/nederland/den%20haag/8584/",
    "Utrecht": "https://www.weerplaza.nl/nederland/utrecht/19344/",
    "Eindhoven": "https://www.weerplaza.nl/nederland/eindhoven/2718/",
    "Groningen": "https://www.weerplaza.nl/nederland/groningen/8274/",
    "Tilburg": "https://www.weerplaza.nl/nederland/tilburg/18904/",
    "Almere": "https://www.weerplaza.nl/nederland/almere/5445/",
    "Breda": "https://www.weerplaza.nl/nederland/breda/2754945/",
    "Nijmegen": "https://www.weerplaza.nl/nederland/nijmegen/14635/",
}

DAY_NUMBERS = {"ma": 0, "di": 1, "wo": 2, "do": 3, "vr": 4, "za": 5, "zo": 6}
DAY_RE = re.compile(r"^(ma|di|wo|do|vr|za|zo)$", re.IGNORECASE)
TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
FORECAST_TEMP_RE = re.compile(r"^(-?\d+(?:[.,]\d+)?)\s*°\s*C$", re.IGNORECASE)
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; TemperatureCollector/1.0)"}


def forecast_columns() -> list[str]:
    return [
        "temperature_now_c" if hour == 0 else f"temperature_plus_{hour}h_c"
        for hour in range(HORIZON_HOURS)
    ]


CSV_COLUMNS = ["sampled_at", *forecast_columns()]
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def resolve_forecast_time(day: str, hour: int, minute: int, now: datetime) -> datetime:
    days_ahead = (DAY_NUMBERS[day.lower()] - now.weekday()) % 7
    forecast_date: date = now.date() + timedelta(days=days_ahead)
    forecast_time = datetime.combine(forecast_date, datetime.min.time(), tzinfo=TIMEZONE).replace(
        hour=hour, minute=minute
    )
    # The current forecast hour may already have started. Keep it as offset zero.
    if forecast_time < now - timedelta(minutes=59):
        forecast_time += timedelta(days=7)
    return forecast_time


def parse_hourly_forecast(lines: list[str], now: datetime) -> dict[int, float]:
    """Read Weerplaza's 48 hourly values and map them to offsets 0 through 47."""
    try:
        start = next(i for i, line in enumerate(lines) if line == "Het weer de komende 48 uur") + 1
    except StopIteration as exc:
        raise ValueError("Weerplaza 48-hour forecast section not found") from exc

    section_end = next(
        (i for i in range(start, len(lines)) if lines[i].startswith("Weersverwachting ")),
        len(lines),
    )
    section = lines[start:section_end]
    current_hour = now.replace(minute=0, second=0, microsecond=0)
    forecasts: dict[int, float] = {}

    for i in range(len(section) - 5):
        day = section[i].lower()
        if not DAY_RE.match(day) or not TIME_RE.match(section[i + 1]):
            continue
        # Weerplaza repeats weekday and time before the forecast temperature.
        if section[i : i + 2] != section[i + 2 : i + 4]:
            continue
        time_match = TIME_RE.match(section[i + 1])
        temp_match = FORECAST_TEMP_RE.match(section[i + 4])
        if not time_match or not temp_match:
            continue

        valid_at = resolve_forecast_time(day, int(time_match.group(1)), int(time_match.group(2)), now)
        delta_seconds = (valid_at - current_hour).total_seconds()
        if delta_seconds < 0 or delta_seconds % 3600 != 0:
            continue
        offset = int(delta_seconds // 3600)
        if offset < HORIZON_HOURS:
            forecasts[offset] = float(temp_match.group(1).replace(",", "."))

    if not forecasts:
        raise ValueError("No hourly forecast temperatures could be parsed")
    return forecasts


def fetch_city_forecast(city: str, url: str, now: datetime) -> dict[str, object]:
    response = requests.get(url, headers=HEADERS, timeout=20)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    lines = [line.strip() for line in soup.get_text("\n").splitlines() if line.strip()]
    forecast = parse_hourly_forecast(lines, now)

    row: dict[str, object] = {"sampled_at": now.isoformat(timespec="seconds")}
    row.update({column: float("nan") for column in forecast_columns()})
    for offset, temperature in forecast.items():
        row[forecast_columns()[offset]] = temperature

    missing = sorted(set(range(HORIZON_HOURS)) - set(forecast))
    if missing:
        logging.warning("%s: forecast is missing hour offsets %s", city, missing)
    return row


def city_file(city: str) -> Path:
    slug = re.sub(r"[^a-z0-9]+", "_", city.lower()).strip("_")
    return OUTPUT_DIR / f"{slug}.csv"


def collect_once() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(TIMEZONE)
    for city, url in CITIES.items():
        path = city_file(city)
        try:
            row = fetch_city_forecast(city, url, now)
        except Exception as exc:
            logging.exception("%s: forecast collection failed: %s", city, exc)
            row = {"sampled_at": now.isoformat(timespec="seconds")}
            row.update({column: float("nan") for column in forecast_columns()})

        pd.DataFrame([row], columns=CSV_COLUMNS).to_csv(
            path,
            mode="a",
            header=not path.exists(),
            index=False,
            na_rep="",
        )
        logging.info("%s: saved one forecast row to %s", city, path)


def seconds_until_next_hour(now: datetime) -> float:
    next_hour = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return max(0.0, (next_hour - now).total_seconds())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="collect one forecast row and exit")
    args = parser.parse_args()
    logging.info("Writing hourly forecast rows to %s", OUTPUT_DIR)
    try:
        while True:
            collect_once()
            if args.once:
                return
            time.sleep(seconds_until_next_hour(datetime.now(TIMEZONE)))
    except KeyboardInterrupt:
        logging.info("Stopped by user")


if __name__ == "__main__":
    main()
