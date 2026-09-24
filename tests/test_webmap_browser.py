"""Browser acceptance scenarios from the UX specification. Requires Playwright Chromium.

Run explicitly: python -m pytest tests/test_webmap_browser.py -q
External Leaflet assets are real; map tiles/photos are blocked in deterministic tests.
"""
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import pandas as pd
import pytest

playwright = pytest.importorskip('playwright.sync_api')
from app.services.export_service import render_webgis

TEMPLATE = Path(__file__).resolve().parents[1] / 'assets/webgis_template.html'


@pytest.mark.parametrize('mobile', [False, True], ids=['desktop', 'mobile'])
def test_acceptance_scenarios(tmp_path, mobile):
    now = datetime.now(ZoneInfo('America/Sao_Paulo'))
    today = now.date()
    saturday = today + timedelta(days=(5 - today.weekday()) % 7)
    # If Sunday, the current weekend includes Sunday.
    weekend = today if today.weekday() == 6 else saturday
    rows = []
    for i in range(7):
        venue = 'A' if i < 4 else 'B' if i < 6 else 'C'
        rows.append({'id': i + 1, 'evento': f'Evento {i + 1}', 'categoria': 'musica' if i != 1 else 'feiras',
                     'data_inicio': weekend.isoformat(), 'data_fim': weekend.isoformat(),
                     'descricao': 'Texto do evento. ' * 90, 'horario_inicio': '19:00', 'local_informado': 'Local ' + venue,
                     'endereco_informado': 'Rua ' + venue + ', 10', 'bairro_informado': 'Trindade',
                     'latitude': -27.59 + (ord(venue) - 65) * .0002, 'longitude': -48.54,
                     'foto_url': 'https://invalid.example/missing.jpg'})
    file = tmp_path / 'qual_a_boa_floripa.html'
    file.write_text(render_webgis(pd.DataFrame(rows), TEMPLATE), encoding='utf-8')
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport={'width': 390 if mobile else 1366, 'height': 844 if mobile else 900},
                                      is_mobile=mobile, has_touch=mobile)
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.route('**/invalid.example/**', lambda route: route.abort())
        page.route('**/tile.openstreetmap.org/**', lambda route: route.abort())
        page.goto(file.as_uri(), wait_until='networkidle')
        page.wait_for_function('window.L && typeof cluster !== "undefined" && cluster !== null', timeout=30000)
        assert page.locator('#counts').inner_text() == '7 eventos · 3 lugares'
        assert page.evaluate('cluster.getLayers().length') == 3
        # 1: Single marker, no badge, opens its single event.
        assert page.evaluate("placeIcon(places[2]).options.html.includes('<b>')") is False
        page.evaluate('cluster.getLayers().find(m=>m.options.eventCount===1).fire("click")')
        assert page.locator('#detailTitle').inner_text() == 'Evento 7'
        assert page.locator('.dot').count() == 0
        page.locator('#closeDetail').click()
        # 2: Four events at one address, one marker and four carousel slides.
        assert page.evaluate("placeIcon(places[0]).options.html.includes('<b>4</b>')")
        page.evaluate('cluster.getLayers().find(m=>m.options.eventCount===4).fire("click")')
        assert '1 de 4' in page.locator('#carouselCount').inner_text()
        assert page.locator('.dot').count() == 4
        page.locator('[data-move="1"]').click()
        assert '2 de 4' in page.locator('#carouselCount').inner_text()
        page.locator('#detail').press('ArrowRight')
        assert '3 de 4' in page.locator('#carouselCount').inner_text()
        page.locator('[data-slide="0"]').click()
        assert '1 de 4' in page.locator('#carouselCount').inner_text()
        # 12: Swipe and vertical-scroll discrimination without a gesture library.
        if mobile:
            page.locator('#detailContent').dispatch_event('pointerdown', {'pointerType': 'touch', 'clientX': 290, 'clientY': 300})
            page.locator('#detailContent').dispatch_event('pointerup', {'pointerType': 'touch', 'clientX': 100, 'clientY': 310})
            assert '2 de 4' in page.locator('#carouselCount').inner_text()
            page.locator('#detailContent').dispatch_event('pointerdown', {'pointerType': 'touch', 'clientX': 200, 'clientY': 400})
            page.locator('#detailContent').dispatch_event('pointerup', {'pointerType': 'touch', 'clientX': 205, 'clientY': 200})
            assert '2 de 4' in page.locator('#carouselCount').inner_text()
            assert page.locator('#detail').evaluate('(e)=>e.scrollHeight>e.clientHeight')
        page.locator('#detail').press('Escape')
        assert not page.locator('#detail').is_visible()
        # 3: Actual Leaflet cluster must count 7 events, not its 3 child markers.
        page.evaluate('map.setView([-27.59,-48.54], 10, {animate:false})')
        page.wait_for_function("document.querySelector('.cluster-icon')?.textContent==='7'")
        # 4/5: Three saved events, two at A. All components use the same subset.
        for event_id in ['1', '2', '5']:
            page.locator(f'#placeList [data-favorite="{event_id}"]').click()
        page.locator('#favoriteFilter').click()
        assert page.locator('#counts').inner_text() == '3 eventos · 2 lugares'
        assert page.locator('#placeList .card').count() == page.locator('#agendaList .card').count() == 3
        assert page.evaluate('cluster.getLayers().map(m=>m.options.eventCount).sort((a,b)=>b-a)') == [2, 1]
        page.evaluate("openEvent('1')")
        assert page.locator('.dot').count() == 2
        page.locator('#detail [data-favorite="1"]').click()
        assert page.locator('#counts').inner_text() == '2 eventos · 2 lugares'
        assert page.locator('#detailTitle').inner_text() == 'Evento 2'
        page.locator('#closeDetail').click()
        # 6: Saved + weekend + music, and random opens the chosen filtered event.
        page.locator('[data-quick="fim"]').click()
        page.locator('[data-category="musica"]').click()
        assert page.locator('#counts').inner_text() == '1 eventos · 1 lugares'
        page.locator('#random').click()
        assert page.locator('#detailTitle').inner_text() == 'Evento 5'
        page.locator('#closeDetail').click()
        # 7/8: Search matches neighborhood and accent-normalized category.
        for query in ['trindade', 'musica']:
            page.locator('#search').fill(query)
            page.wait_for_timeout(180)
            assert page.locator('#placeList .card').count() == 1
        page.locator('#search').fill('no match')
        page.wait_for_timeout(180)
        assert page.locator('#random').is_disabled()
        page.locator('#search').fill('')
        page.locator('#favoriteFilter').click()
        page.locator('[data-category="todos"]').click()
        page.locator('[data-quick="todos"]').click()
        # 9/10/11: Missing optional data, invalid coordinates, unknown category, unsafe HTML.
        page.evaluate("""() => {
            eventos.push(normalizeEvent({id:'missing',nome:'<img src=x onerror=alert(1)>',categoria:'constructor',data_inicio:today(),lat:null,lng:null}));
            eventos.push(normalizeEvent({id:'ongoing',nome:'Exposição',data_inicio:addDays(today(),-3),data_fim:addDays(today(),4),lat:null,lng:null}));
            refresh();
        }""")
        assert page.evaluate('cluster.getLayers().length') == 3
        assert page.locator('#agendaList .card').count() == 9
        assert page.locator('#agendaList .ongoing .card').count() == 1
        assert page.locator('#placeList img[src="x"]').count() == 0
        page.evaluate("openEvent('missing')")
        assert page.locator('#detailTitle').inner_text() == '<img src=x onerror=alert(1)>'
        assert 'Horário a confirmar' in page.locator('#detailContent').inner_text()
        assert page.locator('#detail .original').count() == 0
        assert page.locator('#detail a').count() == 0
        page.locator('#closeDetail').click()
        page.evaluate("openEvent('1')")
        page.wait_for_timeout(150)
        assert page.locator('#detail .hero img').count() == 0
        assert page.locator('#detail .hero span').is_visible()
        # Random selection targets an event within a multi-event venue, not slide 1.
        page.locator('#closeDetail').click()
        page.evaluate('() => { window.originalRandom=Math.random; Math.random=()=>0.3; }')
        expected = page.evaluate('filtered[Math.floor(filtered.length * 0.3)].nome')
        page.locator('#random').click()
        assert page.locator('#detailTitle').inner_text() == expected
        page.evaluate('() => { Math.random=window.originalRandom; }')
        # Native sharing and clipboard fallback both receive the event's details.
        page.evaluate('''() => { Object.defineProperty(navigator,'share',{configurable:true,value:async data=>{window.shared=data;}}); }''')
        page.locator('#share').click()
        assert expected in page.evaluate('window.shared.text')
        page.evaluate('''() => {
            Object.defineProperty(navigator,'share',{configurable:true,value:undefined});
            Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>{window.copied=text;}}});
        }''')
        page.locator('#share').click()
        assert expected in page.evaluate('window.copied')
        # Geolocation denial leaves browsing functional.
        page.locator('#closeDetail').click()
        page.evaluate("() => { navigator.geolocation.getCurrentPosition=(_,fail)=>fail({code:1}); }")
        page.locator('#locate').click()
        assert 'Não foi possível' in page.locator('#notice').inner_text()
        page.evaluate("() => { navigator.geolocation.getCurrentPosition=(ok)=>ok({coords:{latitude:-27.59,longitude:-48.54}}); }")
        page.locator('#locate').click()
        assert page.evaluate("distance(eventos[0])") == '0 m'
        page.locator('#agendaTab').click()
        assert page.locator('#agenda').is_visible()
        assert page.locator('#sheet').is_hidden()
        page.locator('#mapTab').click()
        if mobile:
            page.locator('#sheetToggle').click()
            assert page.locator('#sheetToggle').get_attribute('aria-expanded') == 'true'
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        # Storage survives a reload, and the actual generated file opens via file://.
        page.reload(wait_until='networkidle')
        assert page.evaluate("favorites.has('2') && favorites.has('5') && !favorites.has('1')")
        assert not errors, errors
        browser.close()
