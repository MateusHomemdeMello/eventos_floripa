"""Generate the real output and capture desktop/mobile views for visual review."""
from pathlib import Path
from playwright.sync_api import sync_playwright
from app.services.history_service import PostHistory
from app.services.export_service import generate_webgis

root = Path(__file__).resolve().parents[1]
output = root / 'data/output/qual_a_boa_floripa.html'
generate_webgis(PostHistory(root / 'data/banco_posts.csv').frames()[2], root / 'assets/webgis_template.html', output)
screenshots = root / '.webmap_review'
screenshots.mkdir(exist_ok=True)
with sync_playwright() as p:
    browser = p.chromium.launch()
    for mobile in [False, True]:
        context = browser.new_context(viewport={'width': 390 if mobile else 1440, 'height': 844 if mobile else 1000}, is_mobile=mobile, has_touch=mobile)
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(output.as_uri(), wait_until='networkidle')
        page.wait_for_timeout(1200)
        mode = 'mobile' if mobile else 'desktop'
        page.screenshot(path=str(screenshots / f'{mode}.png'))
        page.locator('#agendaTab').click()
        page.screenshot(path=str(screenshots / f'{mode}_agenda.png'))
        page.locator('#agendaList .card-open').first.click()
        page.screenshot(path=str(screenshots / f'{mode}_detail.png'))
        assert not errors, errors
        print(mode, page.locator('#counts').inner_text(), 'no JS errors')
        context.close()
    browser.close()
