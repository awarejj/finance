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


def _parse_long_bar(content: str) -> tuple[float, float] | tuple[None, None]:
    """Parse --long-percent CSS variable from IG/DailyFX sentiment bar."""
    m = re.search(r'--long-percent:\s*(\d+(?:\.\d+)?)%', content)
    if m:
        long_pct = float(m.group(1))
        return long_pct, round(100 - long_pct, 1)
    return None, None


def _ig_fetch(page) -> list[PositionRow]:
    rows = []
    captured = {}

    # Intercept XHR responses containing sentiment data
    def handle_response(response):
        if "sentiment" in response.url.lower() or "clientsentiment" in response.url.lower():
            try:
                data = response.json()
                long_val = data.get("longPositionPercentage") or data.get("percentLong")
                short_val = data.get("shortPositionPercentage") or data.get("percentShort")
                if long_val:
                    captured[response.url] = (float(long_val), float(short_val))
            except Exception:
                pass

    page.on("response", handle_response)

    for label, url in IG_PAGES.items():
        captured.clear()
        try:
            page.goto(url, timeout=30000, wait_until="domcontentloaded")
            page.wait_for_timeout(4000)
            content = page.content()

            long_pct = short_pct = None

            # Use intercepted XHR data first
            if captured:
                long_pct, short_pct = next(iter(captured.values()))

            # Fallback: CSS variable in sentiment bar (same pattern as DailyFX - shared codebase)
            if not long_pct:
                long_pct, short_pct = _parse_long_bar(content)

            if long_pct and short_pct:
                rows.append(PositionRow("IG", label, round(long_pct, 1), round(short_pct, 1)))
                print(f"  [IG] {label}: {long_pct:.1f}% long / {short_pct:.1f}% short")
            else:
                print(f"  [IG] {label}: could not parse sentiment")
        except Exception as exc:
            print(f"  [IG] {label} failed: {exc}")

    page.remove_listener("response", handle_response)
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
    # Try both known OANDA position ratio URLs
    oanda_urls = [
        "https://www.oanda.com/us-en/trading/position-ratios/",
        "https://www.oanda.com/forex-trading/analysis/open-position-ratios",
        "https://www.oanda.com/us-en/analysis/open-positions/",
    ]

    content = ""
    for url in oanda_urls:
        try:
            page.goto(url, timeout=30000, wait_until="domcontentloaded")
            page.wait_for_timeout(4000)
            content = page.content()
            # If we got a 404-style page, try next URL
            if "doesn't exist" in content or "not found" in content.lower():
                continue
            break
        except Exception:
            continue

    if not content:
        print("  [OANDA] all URLs failed")
        for label in OANDA_INSTRUMENTS:
            print(f"  [OANDA] {label}: not found in page")
        return rows

    # Pattern: JSON with instrument + long/short ratios
    matches = re.findall(
        r'"instrument"\s*:\s*"([A-Z_]+)"[^}]{0,200}?"(?:long|pc_long|percentLong)"\s*:\s*([\d.]+)[^}]{0,100}?"(?:short|pc_short|percentShort)"\s*:\s*([\d.]+)',
        content, re.DOTALL
    )
    found = {m[0]: (float(m[1]), float(m[2])) for m in matches}

    if not found:
        matches = re.findall(
            r'"(?:long|pc_long|percentLong)"\s*:\s*([\d.]+)[^}]{0,100}?"(?:short|pc_short|percentShort)"\s*:\s*([\d.]+)[^}]{0,200}?"instrument"\s*:\s*"([A-Z_]+)"',
            content, re.DOTALL
        )
        found = {m[2]: (float(m[0]), float(m[1])) for m in matches}

    for label, instrument in OANDA_INSTRUMENTS.items():
        if instrument in found:
            long_pct, short_pct = found[instrument]
            rows.append(PositionRow("OANDA", label, round(long_pct, 1), round(short_pct, 1)))
            print(f"  [OANDA] {label}: {long_pct:.1f}% long / {short_pct:.1f}% short")
        else:
            # Try visible table cells
            try:
                row_el = page.query_selector(f'[data-instrument="{instrument}"], tr:has-text("{label}")')
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

            # Confirmed pattern from debug: <div class="price-ticket__long-bar" style="--long-percent: 65%">
            long_pct, short_pct = _parse_long_bar(content)

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
