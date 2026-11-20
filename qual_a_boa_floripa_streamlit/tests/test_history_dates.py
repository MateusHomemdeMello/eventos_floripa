from datetime import date, datetime, timezone
from unittest.mock import patch

import pandas as pd

from app.controllers.pipeline_controller import PipelineController
from app.models.config import AppConfig, Secrets
from app.services.history_service import PostHistory


def test_saved_cce_pin_conflict_is_requeued_for_location(tmp_path):
    history = PostHistory(tmp_path / 'banco.csv')
    event = {'evento': 'Mostra', 'descricao': 'Sessão no CCE da UFSC'}
    history.add_extraction('A', {}, [event])
    history.entries['A']['final']['0'] = {
        **event, 'local_padronizado': 'Caixa Econômica Federal', 'latitude': -27.6, 'longitude': -48.5,
    }
    assert history.pending()[1] == [('A', '0')]
    assert history.frames()[2].iloc[0]['necessita_revisao']
    history.entries['A']['final']['0']['local_padronizado'] = 'Centro de Comunicação e Expressão'
    assert history.pending()[1] == []


def test_location_uses_latest_sao_paulo_day_and_combines_sources(tmp_path):
    controller = PipelineController(AppConfig(), Secrets('', '', ''), tmp_path)
    history = PostHistory(controller.history_path)
    common = {'evento': 'Mostra de Curtas', 'data_inicio': '2099-01-01', 'horario_inicio': '18:00'}
    for key, stamp, venue in [
        ('old', '2026-11-20T02:00:00Z', 'UFSC'),
        ('a', '2026-11-20T23:00:00-03:00', 'UFSC'),
        ('b', '2026-11-21T02:55:00Z', 'CCE da UFSC'),
    ]:
        history.add_extraction(key, {}, [{**common, 'local_informado': venue, 'url_post': key,
                                         'data_analise_ia': stamp}])
    history.save()

    def geocode(frame, here, openai, config, progress, persist):
        assert list(frame['local_informado']) == ['CCE da UFSC']
        persist(0, {'local_padronizado': 'CCE da UFSC', 'latitude': -27.6, 'longitude': -48.5}, None)
        return pd.DataFrame(), pd.DataFrame()

    with patch('app.controllers.pipeline_controller.geocode_events', side_effect=geocode) as here:
        final, failures = controller.location_stage()
        controller.location_stage()
        here.assert_called_once()
    assert failures.empty
    assert PostHistory(controller.history_path).pending()[1] == [('old', '0')]
    assert final.loc[final['_post_id'].isin(['a', 'b']), 'fontes_localizacao'].tolist() == [['a', 'b'], ['a', 'b']]


def test_conflicting_venues_require_review_without_here(tmp_path):
    controller = PipelineController(AppConfig(), Secrets('', '', ''), tmp_path)
    history = PostHistory(controller.history_path)
    for key, venue in [('a', 'Teatro da Ilha'), ('b', 'Teatro da Praça')]:
        history.add_extraction(key, {}, [{
            'url_post': key, 'evento': 'Mostra de Curtas', 'responsavel_evento': 'Cineclube',
            'data_inicio': '2099-01-01', 'data_analise_ia': '2026-11-20T14:00:00-03:00',
            'local_informado': venue,
        }])
    history.save()
    with patch('app.controllers.pipeline_controller.geocode_events') as here:
        _, failures = controller.location_stage()
        here.assert_not_called()
    assert len(failures) == 1
    assert list(failures['urls_publicacoes']) == [['a', 'b']]


def test_history_serializes_dates_in_all_payloads(tmp_path):
    timestamp=pd.Timestamp('2026-09-24T11:22:33.123456789-03:00')
    history=PostHistory(tmp_path/'history.csv')
    event={'evento':'Show','data_publicacao':timestamp,'data_inicio':date(2026,9,26)}
    history.add_extraction('A',{'shortcode':'A','data_publicacao':timestamp},[event])
    history.entries['A']['final']['0']={**event,'data_geolocalizacao':datetime(2026,9,24,tzinfo=timezone.utc),
                                       'candidatos_here':[{'data':timestamp,'ausente':pd.NaT}],
                                       'observacoes':pd.NA}
    history.save()
    entry=PostHistory(history.path).entries['A']
    assert entry['post']['data_publicacao']==timestamp.isoformat()
    assert entry['events'][0]['data_inicio']=='2026-09-26'
    assert entry['final']['0']['data_geolocalizacao']=='2026-09-24T00:00:00+00:00'
    assert entry['final']['0']['candidatos_here'][0]['ausente'] is None
    assert entry['final']['0']['observacoes'] is None


def test_here_callback_saves_timestamp_and_surveys_without_reanalysis(tmp_path):
    controller=PipelineController(AppConfig(),Secrets('','',''),tmp_path)
    history=PostHistory(controller.history_path)
    history.add_extraction('A',{'shortcode':'A'},[{'shortcode':'A','evento':'Show',
                                               'data_inicio':'2099-01-01'}])
    history.save()

    def locate(frame,here,openai,config,progress,on_result):
        result={'latitude':-27.6,'longitude':-48.5,
                'data_publicacao':pd.Timestamp('2026-09-24T11:00:00-03:00')}
        on_result(0,result,None)
        return frame.assign(**result),pd.DataFrame()

    with patch('app.controllers.pipeline_controller.geocode_events',side_effect=locate) as here:
        final,failures=controller.location_stage()
        assert failures.empty and len(final)==1
        controller.location_stage()
        assert here.call_count==1
    saved=PostHistory(controller.history_path).entries['A']['final']['0']
    assert saved['data_publicacao']=='2026-09-24T11:00:00-03:00'
    assert len(pd.read_csv(controller.survey_paths['final']))==1
