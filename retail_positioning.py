"""
Retail broker positioning scraper.
Sources: Myfxbook Community Outlook, IG Client Sentiment
Instruments: AUD/USD, EUR/USD, USD/JPY, XAU/USD
"""

import json
import time
import requests
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional
from tabulate import tabulate

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html, */*",
}

TIMEOUT = 15


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
# Myfxbook Community Outlook
# ---------------------------------------------------------------------------

MYFXBOOK_SYMBOLS = {
    "AUDUSD": "AUD/USD",
    "EURUSD": "EUR/USD",
    "USDJPY": "USD/JPY",
    "XAUUSD": "XAU/USD",
}


def _myfxbook_fetch() -> list[PositionRow]:
    """Fetch from Myfxbook's community outlook JSON endpoint."""
    url = "https://www.myfxbook.com/api/get-community-outlook.json"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        print(f"  [Myfxbook] Request failed: {exc}")
        return []

    if data.get("error"):
        print(f"  [Myfxbook] API error: {data.get('message')}")
        return []

    rows: list[PositionRow] = []
    symbols = data.get("symbols", {}).get("symbol", [])
    if isinstance(symbols, dict):
        symbols = [symbols]

    symbol_map = {s.upper(): s for s in MYFXBOOK_SYMBOLS}

    for sym in symbols:
        name = sym.get("name", "").upper().replace("/", "")
        if name not in symbol_map:
            continue
        try:
            long_pct = float(sym["shortPercentage"])  # Myfxbook field names are swapped vs intuition
            short_pct = float(sym["longPercentage"])
            # Re-check: shortPercentage = % of traders SHORT, longPercentage = % LONG
            long_pct = float(sym["longPercentage"])
            short_pct = float(sym["shortPercentage"])
        except (KeyError, ValueError) as exc:
            print(f"  [Myfxbook] Parse error for {name}: {exc}")
            continue
        rows.append(PositionRow(
            source="Myfxbook",
            instrument=MYFXBOOK_SYMBOLS[name],
            long_pct=round(long_pct, 1),
            short_pct=round(short_pct, 1),
        ))

    return rows


# ---------------------------------------------------------------------------
# IG Client Sentiment
# ---------------------------------------------------------------------------

IG_MARKET_IDS = {
    "AUD/USD": "AUDUSD",
    "EUR/USD": "EURUSD",
    "USD/JPY": "USDJPY",
    "XAU/USD": "CS.D.GOLD.CFD.IP",
}

# IG uses different IDs for different market types
IG_SENTIMENT_IDS = {
    "AUD/USD": "AUDUSD",
    "EUR/USD": "EURUSD",
    "USD/JPY": "USDJPY",
    "XAU/USD": "GOLD",
}


def _ig_fetch() -> list[PositionRow]:
    """Fetch from IG's public client sentiment API."""
    rows: list[PositionRow] = []

    for label, market_id in IG_SENTIMENT_IDS.items():
        url = f"https://api.ig.com/gateway/deal/clientsentiment/{market_id}"
        ig_headers = {
            **HEADERS,
            "X-IG-API-KEY": "",       # public endpoint — key not required for sentiment
            "Content-Type": "application/json; charset=UTF-8",
            "Accept": "application/json; charset=UTF-8",
            "Version": "1",
        }
        try:
            resp = requests.get(url, headers=ig_headers, timeout=TIMEOUT)
            if resp.status_code == 403:
                # Fall back to the IG website embedded JSON
                rows += _ig_website_fetch(label, market_id)
                continue
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
        except Exception as exc:
            print(f"  [IG] {label} failed ({exc}), trying website fallback...")
            rows += _ig_website_fetch(label, market_id)

        time.sleep(0.5)

    return rows


def _ig_website_fetch(label: str, market_id: str) -> list[PositionRow]:
    """Scrape IG client sentiment from their public market page."""
    from bs4 import BeautifulSoup

    slug_map = {
        "AUDUSD": "aud-usd",
        "EURUSD": "eur-usd",
        "USDJPY": "usd-jpy",
        "GOLD": "gold",
    }
    slug = slug_map.get(market_id, market_id.lower())
    url = f"https://www.ig.com/en/forex/markets-to-trade-now/{slug}"

    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        # IG embeds sentiment in a JSON blob in a <script> tag
        for script in soup.find_all("script", type="application/ld+json"):
            pass  # not in LD+JSON

        # Try data attributes on sentiment widget
        widget = soup.find(attrs={"data-sentiment-long": True})
        if widget:
            long_pct = float(widget["data-sentiment-long"])
            short_pct = float(widget["data-sentiment-short"])
            return [PositionRow(
                source="IG",
                instrument=label,
                long_pct=round(long_pct, 1),
                short_pct=round(short_pct, 1),
            )]

        print(f"  [IG website] Could not parse sentiment for {label}")
    except Exception as exc:
        print(f"  [IG website] {label} failed: {exc}")

    return []


# ---------------------------------------------------------------------------
# Myfxbook — Playwright fallback (JS-rendered page)
# ---------------------------------------------------------------------------

def _myfxbook_playwright() -> list[PositionRow]:
    """Use Playwright to scrape Myfxbook's community outlook page."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  [Playwright] Not installed. Run: pip install playwright && playwright install chromium")
        return []

    rows: list[PositionRow] = []
    url = "https://www.myfxbook.com/community/outlook"

    # Use pre-installed Chromium if available (Claude Code cloud environment)
    import os
    chromium_path = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
    launch_kwargs = {"headless": True}
    if os.path.exists(chromium_path):
        launch_kwargs["executable_path"] = chromium_path

    with sync_playwright() as p:
        browser = p.chromium.launch(**launch_kwargs)
        page = browser.new_page()
        page.set_extra_http_headers({"User-Agent": HEADERS["User-Agent"]})

        try:
            page.goto(url, timeout=30000, wait_until="networkidle")
            # Intercept the JSON API call
            page.wait_for_selector("table.community-outlook-table", timeout=15000)

            from bs4 import BeautifulSoup
            soup = BeautifulSoup(page.content(), "lxml")
            rows = _parse_myfxbook_html(soup)
        except Exception as exc:
            print(f"  [Playwright/Myfxbook] Failed: {exc}")
        finally:
            browser.close()

    return rows


def _parse_myfxbook_html(soup) -> list[PositionRow]:
    from bs4 import BeautifulSoup

    target_names = {v: k for k, v in MYFXBOOK_SYMBOLS.items()}
    rows = []

    for row in soup.select("tr"):
        cells = row.find_all("td")
        if len(cells) < 3:
            continue
        name = cells[0].get_text(strip=True).upper().replace("/", "")
        if name not in MYFXBOOK_SYMBOLS:
            continue
        try:
            long_pct = float(cells[1].get_text(strip=True).replace("%", ""))
            short_pct = float(cells[2].get_text(strip=True).replace("%", ""))
            rows.append(PositionRow(
                source="Myfxbook",
                instrument=MYFXBOOK_SYMBOLS[name],
                long_pct=round(long_pct, 1),
                short_pct=round(short_pct, 1),
            ))
        except (ValueError, IndexError):
            continue
    return rows


# ---------------------------------------------------------------------------
# DailyFX SSI (Speculative Sentiment Index) — public page
# ---------------------------------------------------------------------------

DAILYFX_SLUGS = {
    "AUD/USD": "audusd",
    "EUR/USD": "eurusd",
    "USD/JPY": "usdjpy",
    "XAU/USD": "gold",
}


def _dailyfx_fetch() -> list[PositionRow]:
    """Scrape DailyFX SSI data from their sentiment pages."""
    from bs4 import BeautifulSoup

    rows = []
    for label, slug in DAILYFX_SLUGS.items():
        url = f"https://www.dailyfx.com/sentiment/{slug}"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")

            # DailyFX stores sentiment in JSON-LD or data attributes
            # Try script[type="application/json"]
            for script in soup.find_all("script"):
                text = script.string or ""
                if '"longPercentage"' in text or '"percentLong"' in text:
                    try:
                        # Find JSON blob
                        start = text.index("{")
                        data = json.loads(text[start:])
                        long_pct = data.get("longPercentage") or data.get("percentLong")
                        short_pct = data.get("shortPercentage") or data.get("percentShort")
                        if long_pct and short_pct:
                            rows.append(PositionRow(
                                source="DailyFX",
                                instrument=label,
                                long_pct=round(float(long_pct), 1),
                                short_pct=round(float(short_pct), 1),
                            ))
                            break
                    except (ValueError, KeyError):
                        pass

            # Try data-* attributes
            sentiment_el = soup.find(attrs={"data-percent-long": True})
            if sentiment_el and label not in [r.instrument for r in rows if r.source == "DailyFX"]:
                long_pct = float(sentiment_el["data-percent-long"])
                short_pct = 100.0 - long_pct
                rows.append(PositionRow(
                    source="DailyFX",
                    instrument=label,
                    long_pct=round(long_pct, 1),
                    short_pct=round(short_pct, 1),
                ))

        except Exception as exc:
            print(f"  [DailyFX] {label} failed: {exc}")

        time.sleep(0.3)

    return rows


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def fetch_all() -> list[PositionRow]:
    all_rows: list[PositionRow] = []

    print("Fetching Myfxbook Community Outlook...")
    mb = _myfxbook_fetch()
    if mb:
        all_rows += mb
        print(f"  Got {len(mb)} instruments from Myfxbook API")
    else:
        print("  Myfxbook API unavailable, trying Playwright...")
        mb2 = _myfxbook_playwright()
        all_rows += mb2
        print(f"  Got {len(mb2)} instruments via Playwright")

    print("Fetching IG Client Sentiment...")
    ig = _ig_fetch()
    all_rows += ig
    print(f"  Got {len(ig)} instruments from IG")

    print("Fetching DailyFX SSI...")
    dfx = _dailyfx_fetch()
    all_rows += dfx
    print(f"  Got {len(dfx)} instruments from DailyFX")

    return all_rows


def print_table(rows: list[PositionRow]) -> None:
    if not rows:
        print("\nNo data retrieved.")
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

    print("\n" + "=" * 72)
    print("  RETAIL POSITIONING SNAPSHOT")
    print("=" * 72)
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
    print(f"\nData saved to {output_path}")
