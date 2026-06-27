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

IG_PAGE_SLUGS = {
    "AUD/USD": "aud-usd",
    "EUR/USD": "eur-usd",
    "USD/JPY": "usd-jpy",
    "XAU/USD": "gold",
}


def _ig_fetch() -> list[PositionRow]:
    """Scrape IG client sentiment from their public market pages."""
    import re
    from bs4 import BeautifulSoup

    rows: list[PositionRow] = []

    for label, slug in IG_PAGE_SLUGS.items():
        # IG hosts sentiment on their forex/commodities market pages
        if label == "XAU/USD":
            url = f"https://www.ig.com/en/commodities/markets-to-trade-now/{slug}-price"
        else:
            url = f"https://www.ig.com/en/forex/markets-to-trade-now/{slug}-chart"

        try:
            resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")

            long_pct = short_pct = None

            # Try JSON embedded in <script> tags
            for script in soup.find_all("script"):
                text = script.string or ""
                if "longPositionPercentage" in text or "clientSentiment" in text:
                    try:
                        match = re.search(r'"longPositionPercentage"\s*:\s*([\d.]+)', text)
                        if match:
                            long_pct = float(match.group(1))
                        match = re.search(r'"shortPositionPercentage"\s*:\s*([\d.]+)', text)
                        if match:
                            short_pct = float(match.group(1))
                        if long_pct and short_pct:
                            break
                    except Exception:
                        pass

            # Try data attributes on sentiment widget elements
            if not long_pct:
                for attr in ["data-long", "data-sentiment-long", "data-percent-long"]:
                    el = soup.find(attrs={attr: True})
                    if el:
                        long_pct = float(el[attr])
                        short_key = attr.replace("long", "short")
                        short_pct = float(el.get(short_key, 100 - long_pct))
                        break

            # Try plain text pattern "X% Long  Y% Short"
            if not long_pct:
                match = re.search(r'(\d+(?:\.\d+)?)\s*%\s*[Ll]ong.*?(\d+(?:\.\d+)?)\s*%\s*[Ss]hort', resp.text)
                if match:
                    long_pct = float(match.group(1))
                    short_pct = float(match.group(2))

            if long_pct and short_pct:
                rows.append(PositionRow(
                    source="IG",
                    instrument=label,
                    long_pct=round(long_pct, 1),
                    short_pct=round(short_pct, 1),
                ))
                print(f"  [IG] {label}: {long_pct:.1f}% long / {short_pct:.1f}% short")
            else:
                print(f"  [IG] {label}: could not parse sentiment from page")

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

DAILYFX_HEADERS = {
    **HEADERS,
    "Referer": "https://www.dailyfx.com/sentiment",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
}


def _dailyfx_fetch() -> list[PositionRow]:
    import re
    from bs4 import BeautifulSoup

    rows: list[PositionRow] = []
    session = requests.Session()
    session.headers.update(DAILYFX_HEADERS)

    # Warm up: visit main page so session gets cookies
    try:
        session.get("https://www.dailyfx.com/sentiment", timeout=TIMEOUT)
        time.sleep(1)
    except Exception:
        pass

    for label, slug in DAILYFX_SLUGS.items():
        url = f"https://www.dailyfx.com/sentiment/{slug}"
        try:
            resp = session.get(url, timeout=TIMEOUT)
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

# OANDA URL candidates to try in order
OANDA_URL_TEMPLATES = [
    "https://www.oanda.com/forex-trading/analysis/open-position-ratios/{instrument}",
    "https://www.oanda.com/openpositions/{instrument}",
    "https://fxtrade.oanda.com/analysis/open-position-ratios/{instrument}",
]


def _oanda_parse(data: dict) -> tuple[float, float] | tuple[None, None]:
    """Try common response shapes and return (long_pct, short_pct) or (None, None)."""
    import re

    # Flatten one level if wrapped
    d = data.get("data", data)
    if isinstance(d, list) and d:
        d = d[0]

    candidates = [
        (d.get("long"), d.get("short")),
        (d.get("longPositions"), d.get("shortPositions")),
        (d.get("percentLong"), d.get("percentShort")),
        (d.get("pc_long"), d.get("pc_short")),
    ]
    for long_val, short_val in candidates:
        try:
            if long_val is not None and short_val is not None:
                return round(float(long_val), 1), round(float(short_val), 1)
        except (ValueError, TypeError):
            pass
    return None, None


def _oanda_fetch() -> list[PositionRow]:
    """Scrape OANDA open position ratios from their public analysis page."""
    import re
    from bs4 import BeautifulSoup

    rows: list[PositionRow] = []
    session = requests.Session()
    session.headers.update({
        **HEADERS,
        "Referer": "https://www.oanda.com/forex-trading/analysis/open-position-ratios",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })

    # Warm up session
    try:
        session.get("https://www.oanda.com/forex-trading/analysis/open-position-ratios", timeout=TIMEOUT)
        time.sleep(1)
    except Exception:
        pass

    for label, instrument in OANDA_INSTRUMENTS.items():
        long_pct = short_pct = None

        # Try each URL template
        for template in OANDA_URL_TEMPLATES:
            url = template.format(instrument=instrument)
            try:
                resp = session.get(url, timeout=TIMEOUT)
                if resp.status_code == 404:
                    continue
                resp.raise_for_status()

                # Try JSON response first
                try:
                    data = resp.json()
                    long_pct, short_pct = _oanda_parse(data)
                    if long_pct:
                        break
                except ValueError:
                    pass

                # Try scraping HTML
                soup = BeautifulSoup(resp.text, "lxml")

                # Look for JSON embedded in <script> tags
                for script in soup.find_all("script"):
                    text = script.string or ""
                    if instrument.replace("_", "") in text or label.replace("/", "") in text:
                        match = re.search(r'"(?:long|percentLong|pc_long)"\s*:\s*([\d.]+)', text)
                        if match:
                            long_pct = float(match.group(1))
                            match2 = re.search(r'"(?:short|percentShort|pc_short)"\s*:\s*([\d.]+)', text)
                            short_pct = float(match2.group(1)) if match2 else round(100 - long_pct, 1)
                            break

                if long_pct:
                    break

            except Exception:
                continue

        if long_pct and short_pct:
            rows.append(PositionRow(
                source="OANDA",
                instrument=label,
                long_pct=long_pct,
                short_pct=short_pct,
            ))
            print(f"  [OANDA] {label}: {long_pct:.1f}% long / {short_pct:.1f}% short")
        else:
            print(f"  [OANDA] {label}: could not retrieve data")

        time.sleep(0.5)

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
