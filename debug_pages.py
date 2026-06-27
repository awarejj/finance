"""Debug script to inspect raw page content for failing instruments."""

from playwright.sync_api import sync_playwright
import re

PAGES = {
    "dailyfx_usdjpy": "https://www.dailyfx.com/usd-jpy",
    "dailyfx_gold": "https://www.dailyfx.com/gold",
    "ig_audusd": "https://www.ig.com/en/forex/aud-usd",
    "oanda": "https://www.oanda.com/us-en/trading/position-ratios/",
}

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        viewport={"width": 1280, "height": 800},
    )
    page = context.new_page()

    # Capture XHR responses
    xhr_urls = []
    def on_response(resp):
        if any(k in resp.url for k in ["sentiment", "position", "ratio", "long", "short"]):
            xhr_urls.append(resp.url)
    page.on("response", on_response)

    for name, url in PAGES.items():
        xhr_urls.clear()
        print(f"\n{'='*60}")
        print(f"PAGE: {name}")
        print(f"URL:  {url}")
        page.goto(url, timeout=30000, wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        content = page.content()

        print(f"Size: {len(content):,} bytes")

        # Check for --long-percent
        matches = re.findall(r'--long-percent:\s*([\d.]+)%', content)
        print(f"--long-percent values: {matches}")

        # Check for any sentiment/position numbers
        for pattern, label in [
            (r'long["\s:]+(\d+(?:\.\d+)?)', "long values"),
            (r'short["\s:]+(\d+(?:\.\d+)?)', "short values"),
            (r'(\d+(?:\.\d+)?)\s*%', "% values in page"),
        ]:
            hits = re.findall(pattern, content, re.IGNORECASE)[:5]
            if hits:
                print(f"  {label}: {hits}")

        # XHR calls
        if xhr_urls:
            print(f"  XHR sentiment URLs: {xhr_urls[:5]}")

        # Print lines with relevant keywords
        hits = [l.strip() for l in content.splitlines()
                if any(k in l.lower() for k in ["long-percent", "sentiment", "percent", "ratio"])
                and len(l.strip()) < 200]
        print(f"  Relevant lines ({len(hits)}):")
        for h in hits[:10]:
            print(f"    {h}")

    browser.close()
