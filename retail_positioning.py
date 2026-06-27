"""
Retail broker positioning scraper using Playwright.
Sources: IG, OANDA, DailyFX
Instruments: AUD/USD, EUR/USD, USD/JPY, XAU/USD
"""

import json
import re
import time
from datetime import datetime, timezone
from dataclasses import dataclass, field
from tabulate import tabulate


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

IG_PAGES = {
    "AUD/USD": "https://www.ig.com/en/forex/aud-usd",
    "EUR/USD": "https://www.ig.com/en/forex/eur-usd",
    "USD/JPY": "https://www.ig.com/en/forex/usd-jpy",
    "XAU/USD": "https://www.ig.com/en/commodities/gold",
}


def _ig_fetch(page) -> list[PositionRow]:
    rows = []
    for label, url in IG_PAGES.items():
        try:
            page.goto(url, timeout=30000, wait_until="domcontentloaded")
            page.wait_for_timeout(3000)
            content = page.content()

            long_pct = short_pct = None

            # Pattern 1: JSON in page source
            m = re.search(r'"longPositionPercentage"\s*:\s*([\d.]+)', content)
            if m:
                long_pct = float(m.group(1))
                m2 = re.search(r'"shortPositionPercentage"\s*:\s*([\d.]+)', content)
                short_pct = float(m2.group(1)) if m2 else round(100 - long_pct, 1)

            # Pattern 2: "X% long" text on page
            if not long_pct:
                m = re.search(r'(\d+(?:\.\d+)?)\s*%\s*(?:of clients are |)(?:net.)?long', content, re.IGNORECASE)
                if m:
                    long_pct = float(m.group(1))
                    m2 = re.search(r'(\d+(?:\.\d+)?)\s*%\s*(?:of clients are |)(?:net.)?short', content, re.IGNORECASE)
                    short_pct = float(m2.group(1)) if m2 else round(100 - long_pct, 1)

            # Pattern 3: aria-label or data attributes on sentiment bar
            if not long_pct:
                el = page.query_selector('[class*="sentiment"] [class*="long"]')
                if el:
                    text = el.inner_text()
                    m = re.search(r'(\d+(?:\.\d+)?)', text)
                    if m:
                        long_pct = float(m.group(1))
                        short_pct = round(100 - long_pct, 1)

            if long_pct and short_pct:
                rows.append(PositionRow("IG", label, round(long_pct, 1), round(short_pct, 1)))
                print(f"  [IG] {label}: {long_pct:.1f}% long / {short_pct:.1f}% short")
            else:
                print(f"  [IG] {label}: could not parse sentiment")
        except Exception as exc:
            print(f"  [IG] {label} failed: {exc}")
    return rows


# ---------------------------------------------------------------------------
# OANDA Open Position Ratios
# ---------------------------------------------------------------------------

OANDA_INSTRUMENTS = {
    "AUD/USD": "AUD_USD",
    "EUR/USD": "EUR_USD",
    "USD/JPY": "USD_JPY",
    "XAU/USD": "XAU_USD",
}


def _oanda_fetch(page) -> list[PositionRow]:
    rows = []
    url = "https://www.oanda.com/forex-trading/analysis/open-position-ratios"
    try:
        page.goto(url, timeout=30000, wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        content = page.content()

        # OANDA loads all instruments in one page as JSON in a script tag
        # Pattern: {"instrument":"AUD_USD","long":55.2,"short":44.8}
        matches = re.findall(
            r'"instrument"\s*:\s*"([A-Z_]+)"[^}]*?"(?:long|pc_long|percentLong)"\s*:\s*([\d.]+)[^}]*?"(?:short|pc_short|percentShort)"\s*:\s*([\d.]+)',
            content
        )

        found = {m[0]: (float(m[1]), float(m[2])) for m in matches}

        # Also try reversed key order
        if not found:
            matches = re.findall(
                r'"(?:long|pc_long)"\s*:\s*([\d.]+)[^}]*?"(?:short|pc_short)"\s*:\s*([\d.]+)[^}]*?"instrument"\s*:\s*"([A-Z_]+)"',
                content
            )
            found = {m[2]: (float(m[0]), float(m[1])) for m in matches}

        for label, instrument in OANDA_INSTRUMENTS.items():
            if instrument in found:
                long_pct, short_pct = found[instrument]
                rows.append(PositionRow("OANDA", label, round(long_pct, 1), round(short_pct, 1)))
                print(f"  [OANDA] {label}: {long_pct:.1f}% long / {short_pct:.1f}% short")
            else:
                # Try scraping the visible table rows
                try:
                    row_el = page.query_selector(f'[data-instrument="{instrument}"], tr:has-text("{instrument.replace("_", "/")}")')
                    if row_el:
                        text = row_el.inner_text()
                        nums = re.findall(r'(\d+(?:\.\d+)?)\s*%', text)
                        if len(nums) >= 2:
                            rows.append(PositionRow("OANDA", label, float(nums[0]), float(nums[1])))
                            print(f"  [OANDA] {label}: {nums[0]}% long / {nums[1]}% short")
                            continue
                except Exception:
                    pass
                print(f"  [OANDA] {label}: not found in page")

    except Exception as exc:
        print(f"  [OANDA] failed: {exc}")
    return rows


# ---------------------------------------------------------------------------
# DailyFX SSI
# ---------------------------------------------------------------------------

DAILYFX_PAGES = {
    "AUD/USD": "https://www.dailyfx.com/aud-usd",
    "EUR/USD": "https://www.dailyfx.com/eur-usd",
    "USD/JPY": "https://www.dailyfx.com/usd-jpy",
    "XAU/USD": "https://www.dailyfx.com/gold",
}


def _dailyfx_fetch(page) -> list[PositionRow]:
    rows = []
    for label, url in DAILYFX_PAGES.items():
        try:
            page.goto(url, timeout=30000, wait_until="domcontentloaded")
            page.wait_for_timeout(3000)
            content = page.content()

            long_pct = short_pct = None

            # Pattern 1: JSON blob
            for key_long, key_short in [("percentLong", "percentShort"), ("longPercentage", "shortPercentage"), ("pc_long", "pc_short")]:
                m = re.search(rf'"{key_long}"\s*:\s*([\d.]+)', content)
                if m:
                    long_pct = float(m.group(1))
                    m2 = re.search(rf'"{key_short}"\s*:\s*([\d.]+)', content)
                    short_pct = float(m2.group(1)) if m2 else round(100 - long_pct, 1)
                    break

            # Pattern 2: "X% of traders are net-long"
            if not long_pct:
                m = re.search(r'(\d+(?:\.\d+)?)\s*%\s*of\s*(?:retail\s*)?traders?\s*are\s*net.long', content, re.IGNORECASE)
                if m:
                    long_pct = float(m.group(1))
                    short_pct = round(100 - long_pct, 1)

            if long_pct and short_pct:
                rows.append(PositionRow("DailyFX", label, round(long_pct, 1), round(short_pct, 1)))
                print(f"  [DailyFX] {label}: {long_pct:.1f}% long / {short_pct:.1f}% short")
            else:
                print(f"  [DailyFX] {label}: could not parse sentiment")
        except Exception as exc:
            print(f"  [DailyFX] {label} failed: {exc}")
    return rows


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def fetch_all() -> list[PositionRow]:
    from playwright.sync_api import sync_playwright

    all_rows = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )
        page = context.new_page()

        print("Fetching IG Client Sentiment...")
        ig = _ig_fetch(page)
        all_rows += ig
        print(f"  => {len(ig)}/4 instruments\n")

        print("Fetching OANDA Open Position Ratios...")
        oanda = _oanda_fetch(page)
        all_rows += oanda
        print(f"  => {len(oanda)}/4 instruments\n")

        print("Fetching DailyFX SSI...")
        dfx = _dailyfx_fetch(page)
        all_rows += dfx
        print(f"  => {len(dfx)}/4 instruments\n")

        browser.close()

    return all_rows


def print_table(rows: list[PositionRow]) -> None:
    if not rows:
        print("\nNo data retrieved.")
        return

    table = [
        [r.source, r.instrument, f"{r.long_pct:.1f}%", f"{r.short_pct:.1f}%", f"{r.net_long:+.1f}%", r.timestamp]
        for r in sorted(rows, key=lambda x: (x.instrument, x.source))
    ]

    print("=" * 75)
    print("  RETAIL POSITIONING SNAPSHOT")
    print("=" * 75)
    print(tabulate(table, headers=["Source", "Instrument", "% Long", "% Short", "Net Long", "As of"], tablefmt="rounded_outline"))


def to_dict(rows: list[PositionRow]) -> list[dict]:
    return [{"source": r.source, "instrument": r.instrument, "long_pct": r.long_pct,
             "short_pct": r.short_pct, "net_long": r.net_long, "timestamp": r.timestamp}
            for r in rows]


if __name__ == "__main__":
    rows = fetch_all()
    print_table(rows)

    with open("positioning.json", "w") as f:
        json.dump(to_dict(rows), f, indent=2)
    print("\nSaved to positioning.json")
