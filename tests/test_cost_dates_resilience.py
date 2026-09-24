from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest

from app.models.config import AppConfig
from app.services import ai_service as ai, geocoding_service as geo
from app.services.export_service import events_for_webgis


def test_static_prefix_and_duplicate_images(monkeypatch):
    client = Mock()
    client.responses.create.return_value = SimpleNamespace(output_text='{"eventos":[]}')
    download = Mock(return_value='data:image/jpeg;base64,test')
    monkeypatch.setattr(ai, 'image_to_data_url', download)
    for caption in ['primeiro', 'segundo']:
        ai.interpret_post(client, {'data_publicacao': '2026-09-01', 'legenda': caption,
                                  'imagens': ['same', 'same']}, AppConfig())
    calls = client.responses.create.call_args_list
    assert calls[0].kwargs['input'][0] == calls[1].kwargs['input'][0]
    assert calls[0].kwargs['input'][1] != calls[1].kwargs['input'][1]
    assert len(calls[0].kwargs['input'][1]['content']) == 2
    assert download.call_count == 2


@pytest.mark.parametrize('status', [429, 500, 503])
def test_here_transient_retry(monkeypatch, status):
    good = Mock(status_code=200, ok=True)
    good.json.return_value = {'items': [{'id': 'found'}]}
    get = Mock(side_effect=[Mock(status_code=status), good])
    monkeypatch.setattr(geo.requests, 'get', get)
    monkeypatch.setattr(geo.time, 'sleep', lambda _: None)
    assert geo.request_here('https://example.invalid', {}, 'secret') == [{'id': 'found'}]
    assert get.call_count == 2


def test_here_keeps_candidate_when_web_fails(monkeypatch):
    geo.configure('here', 'openai', AppConfig(use_web_fallback=True))
    candidate = {'latitude': -27.6, 'longitude': -48.5, 'necessita_revisao': True}
    monkeypatch.setattr(geo, 'buscar_candidatos_here', lambda _: [candidate])
    monkeypatch.setattr(geo, 'selecionar_melhor_candidato', lambda _: candidate.copy())
    monkeypatch.setattr(geo, 'pesquisar_local_na_web', Mock(side_effect=RuntimeError('offline')))
    assert geo.localizar_evento({})['latitude'] == -27.6


def test_here_does_not_require_openai_without_fallback(monkeypatch):
    client = Mock(side_effect=AssertionError('OpenAI should not be initialized'))
    monkeypatch.setattr(geo, 'OpenAI', client)
    geo.configure('here', '', AppConfig(use_web_fallback=False))
    client.assert_not_called()


def test_expired_events_only_removed_from_view():
    frame = pd.DataFrame([
        {'evento': name, 'data_inicio': '2026-09-01', 'data_fim': end,
         'latitude': -27.6, 'longitude': -48.5}
        for name, end in [('ended', '2026-09-23'), ('ongoing', '2026-09-25'), ('last day', '2026-09-24')]
    ])
    original = frame.copy(deep=True)
    assert [e['nome'] for e in events_for_webgis(frame, date(2026, 9, 24))] == ['ongoing', 'last day']
    pd.testing.assert_frame_equal(frame, original)


def test_end_time_at_last_update():
    frame = pd.DataFrame([{'data_inicio': '2026-09-24', 'data_fim': '2026-09-24',
                           'horario_fim': hour, 'latitude': -27.6, 'longitude': -48.5}
                          for hour in ['10:00', '12:00', '18:00', None]])
    assert len(events_for_webgis(frame, datetime(2026, 9, 24, 12))) == 2
