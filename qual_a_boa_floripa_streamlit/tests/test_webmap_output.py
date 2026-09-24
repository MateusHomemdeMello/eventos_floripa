from pathlib import Path
from datetime import date
import pandas as pd

from app.services.export_service import export_all, events_for_webgis, render_webgis

TEMPLATE = Path(__file__).resolve().parents[1] / 'assets/webgis_template.html'


def test_export_writes_only_one_html(tmp_path):
    frame = pd.DataFrame([{'evento': 'Música', 'data_inicio': '2099-01-01'}])
    result = export_all(frame, frame, frame, frame, tmp_path, TEMPLATE)
    assert result.name == 'qual_a_boa_floripa.html'
    assert [p.name for p in tmp_path.iterdir()] == ['qual_a_boa_floripa.html']
    html = result.read_text(encoding='utf-8')
    assert 'Música' in html and 'let eventos = [' in html
    assert 'events.json' not in html and 'dados.js' not in html


def test_missing_coordinates_preserved_for_agenda_and_stable_ids():
    frame = pd.DataFrame([{'id': 99, '_post_id': 'ABC', '_event_index': 2,
                           'evento': 'Show', 'data_inicio': '2099-01-01'}])
    result = events_for_webgis(frame, date(2099, 1, 1))[0]
    assert result['lat'] is None and result['lng'] is None
    assert result['id'] == 'ABC:2'
    frame['id'] = 1
    assert events_for_webgis(frame, date(2099, 1, 1))[0]['id'] == result['id']


def test_script_termination_is_escaped():
    frame = pd.DataFrame([{'evento': '</script><script>alert(1)</script>',
                           'data_inicio': '2099-01-01'}])
    html = render_webgis(frame, TEMPLATE)
    assert '</script><script>alert(1)' not in html
    assert '<\\/script>' in html
