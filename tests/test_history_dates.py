from datetime import date, datetime, timezone
from unittest.mock import patch

import pandas as pd

from app.controllers.pipeline_controller import PipelineController
from app.models.config import AppConfig, Secrets
from app.services.history_service import PostHistory


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
