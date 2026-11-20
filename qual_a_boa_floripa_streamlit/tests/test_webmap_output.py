from pathlib import Path
from datetime import date
import pandas as pd

from app.services.export_service import consolidate_extractions, export_all, events_for_webgis, render_webgis

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


def test_publishes_one_record_per_occurrence_across_sources():
    original = {
        'evento': 'Show Banda Horizonte', 'data_inicio': '2099-01-01',
        'horario_inicio': '19:00', 'local_informado': 'Teatro da Ilha',
        'responsavel_evento': 'Banda Horizonte', 'bairro_informado': 'Trindade',
    }
    rows = [
        {**original, '_post_id': 'source-a'},
        {**original, '_post_id': 'source-b', 'evento': 'Show: Banda Horizonte!',
         'latitude': -27.6, 'longitude': -48.5, 'url_post': 'https://www.instagram.com/p/B/'},
        {**original, '_post_id': 'late-session', 'horario_inicio': '21:00'},
        {**original, '_post_id': 'other-venue', 'local_informado': 'Teatro do Bairro'},
        {**original, '_post_id': 'other-organizer', 'responsavel_evento': 'Grupo Catarinense'},
        {**original, '_post_id': 'other-date', 'data_inicio': '2099-01-02'},
    ]
    frame = pd.DataFrame(rows)
    before = frame.copy(deep=True)
    events = events_for_webgis(frame, date(2099, 1, 1))
    assert [event['id'] for event in events] == [
        'source-a:0', 'late-session:0', 'other-venue:0', 'other-organizer:0', 'other-date:0',
    ]
    assert events[0]['lat'] == -27.6
    assert events[0]['instagram_url'].endswith('/B/')
    assert events[0]['instagram_urls'] == ['https://www.instagram.com/p/B/']
    pd.testing.assert_frame_equal(frame, before)


def test_extracted_table_groups_only_within_sao_paulo_analysis_day():
    occurrence = {'evento': 'Mostra de Curtas', 'local_informado': 'Sala de Cinema',
                  'data_inicio': '2099-01-01', 'horario_inicio': '18:00'}
    first, second, third = [f'https://www.instagram.com/p/{post}/' for post in ('A', 'B', 'C')]
    rows = [
        {**occurrence, 'data_analise_ia': '2026-11-20T23:50:00-03:00', 'url_post': first},
        {**occurrence, 'data_analise_ia': '2026-11-21T02:55:00Z', 'url_post': second},
        {**occurrence, 'data_analise_ia': '2026-11-21T03:05:00Z', 'url_post': third},
    ]
    frame = pd.DataFrame(rows)
    daily = consolidate_extractions(frame)
    assert list(daily['urls_publicacoes']) == [[first, second], [third]]
    assert len(frame) == 3
    public = events_for_webgis(frame, date(2099, 1, 1))
    assert len(public) == 1
    assert public[0]['instagram_urls'] == [first, second, third]


def test_legacy_records_are_filtered_and_conflicting_campus_pin_is_removed():
    frame = pd.DataFrame([
        {'evento': 'Rematrícula para alunos veteranos do Sesc'},
        {'evento': 'Marcha para Jesus', 'categoria': 'esportes'},
        {'evento': 'Mostra de Cinema', 'descricao': 'Exibição no CCE da UFSC',
         'local_padronizado': 'Caixa Econômica Federal', 'endereco': 'Agência Trindade',
         'latitude': -27.6, 'longitude': -48.5},
        {'evento': 'Oficina no Centro Cultural', 'descricao': 'Organizada pelo CCE da UFSC.',
         'local_padronizado': 'Centro Cultural', 'latitude': -27.6, 'longitude': -48.5},
    ]).assign(data_inicio='2099-01-01')
    screening, workshop = events_for_webgis(frame, date(2099, 1, 1))
    assert screening['local'] == 'CCE da UFSC'
    assert screening['lat'] is None and screening['lng'] is None
    assert screening['endereco'] == ''
    assert workshop['lat'] == -27.6
