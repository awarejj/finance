"""
Retail broker positioning scraper.
Sources: IG Client Sentiment, DailyFX SSI, OANDA Order Book
Instruments: AUD/USD, EUR/USD, USD/JPY, XAU/USD
"""

import json
import time
import requests
from datetime import datetime, timezone
from dataclasses import dataclass, field
from tabulate import tabulate

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html, */*",
}

TIMEOUT = 20


@dataclass
class PositionRow:
    source: str
    instrument: str
    long_pct: float
    short_pct: float
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))

    @property
    def net_long(self) -> float:
        return round(self.long_pct - self.short_pct, 1)


# ---------------------------------------------------------------------------
# IG Client Sentiment
# ---------------------------------------------------------------------------

IG_SENTIMENT_IDS = {
    "AUD/USD": "AUDUSD",
    "EUR/USD": "EURUSD",
    "USD/JPY": "USDJPY",
    "XAU/USD": "GOLD",
}


def _ig_fetch() -> list[PositionRow]:
    rows: list[PositionRow] = []

    for label, market_id in IG_SENTIMENT_IDS.items():
        url = f"https://api.ig.com/gateway/deal/clientsentiment/{market_id}"
        headers = {
            **HEADERS,
            "Content-Type": "application/json; charset=UTF-8",
            "Accept": "application/json; charset=UTF-8",
            "Version": "1",
        }
        try:
            resp = requests.get(url, headers=headers, timeout=TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            long_pct = float(data["longPositionPercentage"])
            short_pct = float(data["shortPositionPercentage"])
            rows.append(PositionRow(
                source="IG",
                instrument=label,
                long_pct=round(long_pct, 1),
                short_pct=round(short_pct, 1),
            ))
            print(f"  [IG] {label}: {long_pct:.1f}% long / {short_pct:.1f}% short")
        except Exception as exc:
            print(f"  [IG] {label} failed: {exc}")

        time.sleep(0.5)

    return rows


# ---------------------------------------------------------------------------
# DailyFX SSI (Speculative Sentiment Index)
# ---------------------------------------------------------------------------

DAILYFX_SLUGS = {
    "AUD/USD": "aud-usd",
    "EUR/USD": "eur-usd",
    "USD/JPY": "usd-jpy",
    "XAU/USD": "gold",
}


def _dailyfx_fetch() -> list[PositionRow]:
    from bs4 import BeautifulSoup

    rows: list[PositionRow] = []

    for label, slug in DAILYFX_SLUGS.items():
        url = f"https://www.dailyfx.com/sentiment/{slug}"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")

            # DailyFX embeds sentiment in a JSON blob inside a <script> tag
            long_pct = short_pct = None
            for script in soup.find_all("script"):
                text = script.string or ""
                if "percentLong" in text or "longPercentage" in text:
                    try:
                        idx = text.index("{")
                        data = json.loads(text[idx:])
                        long_pct = float(data.get("percentLong") or data.get("longPercentage") or 0)
                        short_pct = float(data.get("percentShort") or data.get("shortPercentage") or 0)
                        if long_pct:
                            break
                    except Exception:
                        pass

            # Fallback: data-* attributes on sentiment widget
            if not long_pct:
                el = soup.find(attrs={"data-percent-long": True})
                if el:
                    long_pct = float(el["data-percent-long"])
                    short_pct = 100.0 - long_pct

            # Fallback: look for text like "68% of traders are net-long"
            if not long_pct:
                import re
                match = re.search(r"(\d+(?:\.\d+)?)\s*%\s*of\s*(?:retail\s*)?traders?\s*are\s*net.long", resp.text, re.IGNORECASE)
                if match:
                    long_pct = float(match.group(1))
                    short_pct = round(100.0 - long_pct, 1)

            if long_pct:
                rows.append(PositionRow(
                    source="DailyFX",
                    instrument=label,
                    long_pct=round(long_pct, 1),
                    short_pct=round(short_pct, 1),
                ))
                print(f"  [DailyFX] {label}: {long_pct:.1f}% long / {short_pct:.1f}% short")
            else:
                print(f"  [DailyFX] {label}: could not parse sentiment data")

        except Exception as exc:
            print(f"  [DailyFX] {label} failed: {exc}")

        time.sleep(0.5)

    return rows


# ---------------------------------------------------------------------------
# OANDA fxTrade Sentiment (public open positions page)
# ---------------------------------------------------------------------------

OANDA_INSTRUMENTS = {
    "AUD/USD": "AUD_USD",
    "EUR/USD": "EUR_USD",
    "USD/JPY": "USD_JPY",
    "XAU/USD": "XAU_USD",
}


def _oanda_fetch() -> list[PositionRow]:
    """Fetch OANDA open position ratios from their public lab API."""
    rows: list[PositionRow] = []

    for label, instrument in OANDA_INSTRUMENTS.items():
        url = f"https://www.oanda.com/cfds/open-position-ratios/{instrument}/latest"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            resp.raise_for_status()
            data = resp.json()

            # Response: {"data": {"long": 55.2, "short": 44.8, ...}}
            d = data.get("data", data)
            long_pct = float(d.get("long") or d.get("longPositions") or 0)
            short_pct = float(d.get("short") or d.get("shortPositions") or 0)

            if long_pct:
                rows.append(PositionRow(
                    source="OANDA",
                    instrument=label,
                    long_pct=round(long_pct, 1),
                    short_pct=round(short_pct, 1),
                ))
                print(f"  [OANDA] {label}: {long_pct:.1f}% long / {short_pct:.1f}% short")
            else:
                print(f"  [OANDA] {label}: unexpected response format")
        except Exception as exc:
            print(f"  [OANDA] {label} failed: {exc}")

        time.sleep(0.3)

    return rows


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def fetch_all() -> list[PositionRow]:
    all_rows: list[PositionRow] = []

    print("Fetching IG Client Sentiment...")
    ig = _ig_fetch()
    all_rows += ig
    print(f"  => {len(ig)}/4 instruments\n")

    print("Fetching DailyFX SSI...")
    dfx = _dailyfx_fetch()
    all_rows += dfx
    print(f"  => {len(dfx)}/4 instruments\n")

    print("Fetching OANDA Open Position Ratios...")
    oanda = _oanda_fetch()
    all_rows += oanda
    print(f"  => {len(oanda)}/4 instruments\n")

    return all_rows


def print_table(rows: list[PositionRow]) -> None:
    if not rows:
        print("\nNo data retrieved. Check your internet connection and try again.")
        return

    table = []
    for r in sorted(rows, key=lambda x: (x.instrument, x.source)):
        table.append([
            r.source,
            r.instrument,
            f"{r.long_pct:.1f}%",
            f"{r.short_pct:.1f}%",
            f"{r.net_long:+.1f}%",
            r.timestamp,
        ])

    print("=" * 75)
    print("  RETAIL POSITIONING SNAPSHOT")
    print("=" * 75)
    print(tabulate(
        table,
        headers=["Source", "Instrument", "% Long", "% Short", "Net Long", "As of"],
        tablefmt="rounded_outline",
    ))


def to_dict(rows: list[PositionRow]) -> list[dict]:
    return [
        {
            "source": r.source,
            "instrument": r.instrument,
            "long_pct": r.long_pct,
            "short_pct": r.short_pct,
            "net_long": r.net_long,
            "timestamp": r.timestamp,
        }
        for r in rows
    ]


if __name__ == "__main__":
    rows = fetch_all()
    print_table(rows)

    output_path = "positioning.json"
    with open(output_path, "w") as f:
        json.dump(to_dict(rows), f, indent=2)
    print(f"\nSaved to {output_path}")
