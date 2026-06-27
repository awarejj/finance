"""Debug: find correct URLs and IG sentiment API call."""

from playwright.sync_api import sync_playwright
import re

# Alternative DailyFX URLs to try for USD/JPY and XAU/USD
DAILYFX_ALT = {
    "usd-jpy": [
        "https://www.dailyfx.com/usd-jpy",
        "https://www.dailyfx.com/usd-jpy-rate-forecast",
        "https://www.dailyfx.com/japanese-yen",
        "https://www.dailyfx.com/usdjpy",
    ],
    "gold": [
        "https://www.dailyfx.com/gold",
        "https://www.dailyfx.com/xau-usd",
        "https://www.dailyfx.com/gold-price",
        "https://www.dailyfx.com/xauusd",
    ],
}

# Alternative OANDA URLs
OANDA_ALTS = [
    "https://www.oanda.com/us-en/trading/open-position-ratios/",
    "https://www.oanda.com/us-en/analysis/",
    "https://www.oanda.com/us-en/trading/",
    "https://www.oanda.com/forex-trading/analysis/",
    "https://www.oanda.com/us-en/trading/market-pulse/",
]

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        viewport={"width": 1280, "height": 800},
    )
    page = context.new_page()

    # --- DailyFX URL search ---
    print("=" * 60)
    print("DAILYFX: searching for working URLs")
    for key, urls in DAILYFX_ALT.items():
        print(f"\n  [{key}]")
        for url in urls:
            try:
                resp = page.goto(url, timeout=15000, wait_until="domcontentloaded")
                size = len(page.content())
                long_vals = re.findall(r'--long-percent:\s*([\d.]+)%', page.content())
                print(f"    {url}")
                print(f"      -> {size:,} bytes | --long-percent: {long_vals} | final URL: {page.url}")
                if size > 1000:
                    break  # found a working URL
            except Exception as exc:
                print(f"    {url} -> FAILED: {exc}")

    # --- OANDA URL search ---
    print("\n" + "=" * 60)
    print("OANDA: searching for working URLs")
    for url in OANDA_ALTS:
        try:
            page.goto(url, timeout=15000, wait_until="domcontentloaded")
            size = len(page.content())
            is_404 = "doesn't exist" in page.content() or "not found" in page.content().lower()
            print(f"  {url}")
            print(f"    -> {size:,} bytes | 404={is_404} | final URL: {page.url}")
        except Exception as exc:
            print(f"  {url} -> FAILED: {exc}")

    # --- IG: wait for network idle and capture ALL API calls ---
    print("\n" + "=" * 60)
    print("IG: capturing all API/XHR calls on AUD/USD page")
    api_calls = []

    def on_response(resp):
        ct = resp.headers.get("content-type", "")
        if "json" in ct or "sentiment" in resp.url.lower() or "position" in resp.url.lower():
            try:
                data = resp.json()
                api_calls.append((resp.url, data))
            except Exception:
                api_calls.append((resp.url, None))

    page.on("response", on_response)
    page.goto("https://www.ig.com/en/forex/aud-usd", timeout=30000, wait_until="networkidle")
    page.wait_for_timeout(5000)

    print(f"  Captured {len(api_calls)} JSON API calls:")
    for url, data in api_calls[:20]:
        preview = str(data)[:120] if data else "(non-JSON)"
        print(f"  {url}")
        print(f"    {preview}")

    browser.close()
