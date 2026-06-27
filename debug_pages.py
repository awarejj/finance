"""
Saves raw HTML from each source so we can inspect the actual page structure.
Run this, then share the output files so we can fix the parsers.
"""

from playwright.sync_api import sync_playwright

PAGES = {
    "ig_audusd": "https://www.ig.com/en/forex/aud-usd",
    "oanda_positions": "https://www.oanda.com/forex-trading/analysis/open-position-ratios",
    "dailyfx_audusd": "https://www.dailyfx.com/aud-usd",
}

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

    for name, url in PAGES.items():
        print(f"Loading {url} ...")
        try:
            page.goto(url, timeout=30000, wait_until="domcontentloaded")
            page.wait_for_timeout(4000)
            content = page.content()
            fname = f"{name}.html"
            with open(fname, "w", encoding="utf-8") as f:
                f.write(content)
            print(f"  Saved {fname} ({len(content):,} bytes)")

            # Print any lines containing sentiment-related keywords
            keywords = ["long", "short", "sentiment", "position", "percent", "ratio"]
            hits = []
            for line in content.splitlines():
                lower = line.lower()
                if any(k in lower for k in keywords) and len(line.strip()) < 300:
                    hits.append(line.strip())
            print(f"  Keyword hits ({len(hits)} lines):")
            for h in hits[:20]:
                print(f"    {h}")
            print()
        except Exception as exc:
            print(f"  Failed: {exc}\n")

    browser.close()
print("Done. Check the .html files for full page content.")
