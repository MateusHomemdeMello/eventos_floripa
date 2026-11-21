from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
from datetime import date

from app.controllers.pipeline_controller import PipelineController
from app.models.config import AppConfig, Secrets
from app.services import ai_service, geocoding_service as here
from app.services.export_service import events_for_webgis
from app.services.history_service import PostHistory
from app.services.spatial_quality import AUTO, CONFLICT, MANUAL, REVIEW, UNKNOWN


TEMPLATE = Path(__file__).resolve().parents[1] / 'assets/webgis_template.html'


def candidate(name, *, ident=None, city='Florianópolis', street='Rua Marechal Guilherme',
              number='26', kind='place', district='Centro', precision='PA'):
    return {
        'id': ident, 'title': name, 'resultType': kind, 'houseNumberType': precision,
        'position': {'lat': -27.59, 'lng': -48.54}, 'access': [{'lat': -27.591, 'lng': -48.541}],
        'address': {'label': f'{street}, {number}, {district}, {city}', 'street': street,
                    'houseNumber': number, 'city': city, 'district': district, 'countryCode': 'BRA'},
        'scoring': {'queryScore': 1},
    }


def mocked_here(monkeypatch, discover=(), geocode=()):
    here.configure('fake', '', AppConfig())
    discover_call = Mock(return_value=discover)
    geocode_call = Mock(return_value=geocode)
    monkeypatch.setattr(here, 'pesquisar_here_discover', discover_call)
    monkeypatch.setattr(here, 'pesquisar_here_geocode', geocode_call)
    return discover_call, geocode_call


def test_organizer_is_separate_and_wrong_address_cannot_win(monkeypatch):
    caption = 'UFSC apresenta mostra no Teatro Álvaro de Carvalho, Rua Marechal Guilherme, 26, Centro.'
    event = {'evento': 'Mostra Cultural', 'responsavel_evento': 'UFSC',
             'local_informado': 'Teatro Álvaro de Carvalho', 'endereco_informado': 'Rua Marechal Guilherme, 26',
             'bairro_informado': 'Centro', 'evidencia_local': 'no Teatro Álvaro de Carvalho',
             'origem_evidencia_local': 'legenda', 'evidencia_endereco': 'Rua Marechal Guilherme, 26, Centro',
             'origem_evidencia_endereco': 'legenda'}
    event = ai_service.validate_event(event, caption, set())
    wrong_number = candidate('Teatro Álvaro de Carvalho', ident='wrong-number', number='260')
    wrong_org = candidate('UFSC', ident='ufsc', street='Rua Delfino Conti', number='10')
    right = candidate('Teatro Álvaro de Carvalho', ident='tac')
    discover, _ = mocked_here(monkeypatch, [wrong_org, wrong_number, right])
    result = here.localizar_evento(event)
    assert result['status_localizacao'] == AUTO and result['here_id'] == 'tac'
    assert {row['here_id'] for row in result['candidatos_rejeitados_here']} == {'ufsc', 'wrong-number'}
    assert all('UFSC' not in call.args[0] for call in discover.call_args_list)
    assert result['acessos_here'][0]['lat'] != result['latitude']


def test_specific_auditorium_is_not_replaced_with_campus(monkeypatch):
    event = {'local_informado': 'Auditório Garapuvu', 'instituicao_local': 'UFSC'}
    campus = candidate('Universidade Federal de Santa Catarina', ident='campus')
    room = candidate('Auditório Garapuvu', ident='room')
    mocked_here(monkeypatch, [campus, room])
    result = here.localizar_evento(event)
    assert result['here_id'] == 'room' and result['precisao_localizacao'] == 'poi'


def test_address_only_distinguishes_point_from_interpolation(monkeypatch):
    address = {'endereco_informado': 'Rua Marechal Guilherme, nº 26', 'responsavel_evento': 'UFSC'}
    point = candidate('Rua Marechal Guilherme, 26', kind='houseNumber')
    discover, geocode = mocked_here(monkeypatch, geocode=[point])
    result = here.localizar_evento(address)
    assert result['status_localizacao'] == AUTO
    assert result['precisao_localizacao'] == 'endereco' and result['local_padronizado'] is None
    discover.assert_not_called()
    assert '26' in geocode.call_args.args[0]
    point['houseNumberType'] = 'interpolated'
    assert here.localizar_evento(address)['status_localizacao'] == REVIEW


def test_footer_address_and_institution_only_do_not_generate_query(monkeypatch):
    event = ai_service.validate_event(
        {'evento': 'Mostra', 'local_informado': 'Teatro', 'origem_evidencia_local': 'imagem 1',
         'evidencia_local': 'Local: Teatro', 'endereco_informado': 'Rua da sede, 100',
         'origem_evidencia_endereco': 'legenda', 'evidencia_endereco': 'Endereço para inscrições: Rua da sede, 100'},
        'Endereço para inscrições: Rua da sede, 100', {'imagem 1'},
    )
    assert event['local_informado'] == 'Teatro' and event['endereco_informado'] is None
    discover, geocode = mocked_here(monkeypatch)
    result = here.localizar_evento({'evento': 'Espetáculo', 'responsavel_evento': 'Secretaria de Cultura',
                                  'local_informado': 'Secretaria de Cultura'})
    assert result['status_localizacao'] == UNKNOWN
    discover.assert_not_called()
    geocode.assert_not_called()
    events = events_for_webgis(pd.DataFrame([{**result, 'evento': 'Espetáculo',
                                            'data_inicio': '2099-01-01', 'latitude': -27.6, 'longitude': -48.5}]),
                               date(2099, 1, 1))
    assert len(events) == 1 and events[0]['lat'] is None


def test_homonyms_are_checked_against_explicit_territory(monkeypatch):
    points = [candidate('Teatro da Ilha', ident='floripa'),
              candidate('Teatro da Ilha', ident='saojose', city='São José')]
    points[1]['scoring']['queryScore'] = 0
    mocked_here(monkeypatch, points)
    event = {'local_informado': 'Teatro da Ilha'}
    assert here.localizar_evento(event)['status_localizacao'] == REVIEW
    assert here.localizar_evento({**event, 'municipio_informado': 'São José'})['here_id'] == 'saojose'


def test_same_coordinate_does_not_merge_two_distinct_candidate_ids(monkeypatch):
    mocked_here(monkeypatch, [candidate('Auditório Garapuvu', ident='one'),
                             candidate('Auditório Garapuvu', ident='two')])
    result = here.localizar_evento({'local_informado': 'Auditório Garapuvu'})
    assert result['status_localizacao'] == REVIEW
    assert len(result['candidatos_here']) == 2
    assert not here.avaliar_candidato_here({'local_informado': 'Auditório Garapuvu'},
                                            candidate('Auditório Açores', ident='other'), '', 'discover')


def scenario_controller(tmp_path):
    template = tmp_path / 'assets' / TEMPLATE.name
    template.parent.mkdir()
    template.write_bytes(TEMPLATE.read_bytes())
    return PipelineController(AppConfig(), Secrets('', '', 'fake'), tmp_path)


def test_conflict_persists_as_an_independent_state_and_holds_agenda(tmp_path, monkeypatch):
    controller = scenario_controller(tmp_path)
    history = PostHistory(controller.history_path)
    common = {'evento': 'Mostra de Curtas Catarinenses', 'responsavel_evento': 'Cineclube',
              'data_inicio': '2099-01-01'}
    history.add_extraction('a', {}, [{**common, 'local_informado': 'Teatro da Ilha'}])
    history.add_extraction('b', {}, [{**common, 'local_informado': 'Sala do CCE'}])
    history.save()
    remote = Mock(side_effect=AssertionError('No paid calls'))
    monkeypatch.setattr('app.controllers.pipeline_controller.geocode_events', remote)
    final, failures = controller.location_stage()
    assert set(final['status_localizacao']) == {CONFLICT}
    assert len(failures.iloc[0]['evidencias_fontes']) == 2
    assert not events_for_webgis(final, date(2099, 1, 1))
    remote.assert_not_called()
    assert PostHistory(controller.history_path).entries['a']['events'][0]['local_informado'] == 'Teatro da Ilha'


def test_manual_correction_and_registry_survive_resume(tmp_path, monkeypatch):
    controller = scenario_controller(tmp_path)
    history = PostHistory(controller.history_path)
    event = {'evento': 'Mostra', 'local_informado': 'TAC', 'data_inicio': '2099-01-01'}
    history.add_extraction('a', {}, [event])
    history.save()
    point = {'local_padronizado': 'Teatro Álvaro de Carvalho', 'municipio': 'Florianópolis',
             'endereco': 'Rua Marechal Guilherme, 26', 'latitude': -27.59, 'longitude': -48.54}
    controller.review_location('a', 0, action='aprovar', reason='Conferido na fonte',
                               location=point, aliases=['TAC'], register=True)
    with PostHistory.transaction(controller.history_path) as history:
        history.add_extraction('b', {}, [{**event, 'evento': 'Outra Mostra'}])
        history.checkpoint('b')
    remote = Mock(side_effect=AssertionError('Registry should avoid remote calls'))
    monkeypatch.setattr('app.controllers.pipeline_controller.geocode_events', remote)
    final, _ = controller.location_stage()
    assert final.set_index('_post_id').loc['b', 'origem_localizacao'] == 'cadastro_locais'
    assert final.set_index('_post_id').loc['b', 'status_localizacao'] == AUTO
    assert final.set_index('_post_id').loc['a', 'status_localizacao'] == MANUAL
    remote.assert_not_called()
    controller.review_location('b', 0, action='corrigir_endereco', reason='Nova unidade',
                               location={'endereco_informado': 'Rua Diferente, 10'})
    assert PostHistory(controller.history_path).pending()[1] == [('b', '0')]


def test_gpt_strict_response_preserves_multiple_sessions_and_validates_clock():
    client = Mock()
    client.responses.create.return_value = SimpleNamespace(
        status='completed',
        output_text='{"eventos":[{"evento":"Mostra","data_inicio":"2026-11-20","horario_inicio":"24:70"},'
                    '{"evento":"Mostra","data_inicio":"2026-11-22","horario_inicio":"18:30"}]}',
    )
    events, _ = ai_service.interpret_post(client, {'data_publicacao': '2026-11-20'}, AppConfig())
    assert [event['data_inicio'] for event in events] == ['2026-11-20', '2026-11-22']
    assert [event['horario_inicio'] for event in events] == [None, '18:30']
