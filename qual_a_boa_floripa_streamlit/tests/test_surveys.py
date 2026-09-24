from pathlib import Path
from unittest.mock import patch
import json
import pandas as pd
import pytest

from app.controllers.pipeline_controller import PipelineController
from app.models.config import AppConfig, Secrets
from app.services.history_service import PostHistory
from app.services.survey_service import append_survey, DATE_COLUMNS


@pytest.fixture
def controller(tmp_path):
    (tmp_path/'assets').mkdir()
    (tmp_path/'assets/webgis_template.html').write_text('/* EVENTOS_INICIO */ let eventos = []; /* EVENTOS_FIM */',encoding='utf-8')
    return PipelineController(AppConfig(),Secrets('a','b','c'),tmp_path)


def collection(*keys):
    return pd.DataFrame([{'shortcode':key,'url_post':f'https://www.instagram.com/p/{key}/',
                          'legenda':'Show, música\nhoje','imagens':['https://example.com/a.jpg']} for key in keys])


def extract(_,frame,*args):
    return pd.DataFrame([{'shortcode':r.shortcode,'evento':'Show '+r.shortcode,
                          'data_inicio':'2099-01-01'} for r in frame.itertuples()]),pd.DataFrame()


def locate(frame,*args):
    return frame.assign(latitude=-27.6,longitude=-48.5),pd.DataFrame()


def read(controller,stage):
    return pd.read_csv(controller.survey_paths[stage],keep_default_na=False)


def test_cumulative_surveys_preserve_dates_without_duplicate_cached_results(controller):
    with patch('app.controllers.pipeline_controller.collect_posts',return_value=collection('A')) as scraper, patch('app.controllers.pipeline_controller.extract_events',side_effect=extract) as ai, patch('app.controllers.pipeline_controller.geocode_events',side_effect=locate) as here:
        first=controller.run()
        original=read(controller,'final').iloc[0].to_dict()
        for name in DATE_COLUMNS:
            assert first.final[name].iloc[0]
            assert pd.Timestamp(first.final[name].iloc[0]).utcoffset().total_seconds()==-10800
        assert first.final.data_scraping.iloc[0] <= first.final.data_analise_ia.iloc[0] <= first.final.data_geolocalizacao.iloc[0]
        controller.run()
        assert ai.call_count==here.call_count==1
        assert len(read(controller,'posts'))==2
        assert len(read(controller,'events'))==len(read(controller,'final'))==1
        assert read(controller,'final').iloc[0].to_dict()==original
        scraper.return_value=collection('A','B')
        controller.run()
        assert len(read(controller,'posts'))==4
        assert len(read(controller,'events'))==len(read(controller,'final'))==2
        assert read(controller,'final').iloc[0].to_dict()==original
        before={key:path.read_bytes() for key,path in controller.survey_paths.items()}
        controller.restore_state()
        controller.export_stage()
        assert before=={key:path.read_bytes() for key,path in controller.survey_paths.items()}
        assert json.loads(read(controller,'posts').iloc[0]['imagens'])==['https://example.com/a.jpg']


def test_partial_location_persists_and_retry_only_appends_new_result(controller):
    def partial(frame,key,openai,config,progress,on_result):
        on_result(0,{'latitude':-27.6,'longitude':-48.5},None)
        raise RuntimeError('interrupted')
    with patch('app.controllers.pipeline_controller.collect_posts',return_value=collection('A','B')), patch('app.controllers.pipeline_controller.extract_events',side_effect=extract), patch('app.controllers.pipeline_controller.geocode_events',side_effect=partial) as here:
        controller.collect_stage()
        controller.extraction_stage()
        assert read(controller,'final').empty
        with pytest.raises(RuntimeError,match='interrupted'):
            controller.location_stage()
        assert len(read(controller,'final'))==1
        here.side_effect=locate
        controller.location_stage()
        assert len(read(controller,'final'))==2


def test_legacy_dates_not_fabricated(controller):
    history=PostHistory(controller.history_path)
    event={'shortcode':'A','evento':'Old event','data_inicio':'2099-01-01'}
    history.add_extraction('A',{'shortcode':'A'},[event])
    history.entries['A']['final']['0']={**event,'latitude':-27.6,'longitude':-48.5}
    history.save()
    stages,_=controller.restore_state()
    for stage in ['posts','events','final']:
        frame=read(controller,stage)
        assert len(frame)==1
        assert all(frame[name].iloc[0]=='' for name in DATE_COLUMNS)
        assert set(DATE_COLUMNS).issubset(stages[stage].columns)
    controller.restore_state()
    assert len(read(controller,'final'))==1


def test_atomic_append_schema_growth_and_failure(tmp_path):
    path=tmp_path/'posts.csv'
    first=pd.DataFrame([{'shortcode':'A','id_coleta':'one','data_scraping':'2026-09-24T12:00:00-03:00'}])
    append_survey(path,'posts',first)
    before=path.read_bytes()
    second=pd.DataFrame([{'shortcode':'B','id_coleta':'two','new_field':'new'}])
    with patch('app.services.survey_service.os.replace',side_effect=OSError('locked')):
        with pytest.raises(OSError): append_survey(path,'posts',second)
    assert path.read_bytes()==before
    append_survey(path,'posts',second)
    frame=pd.read_csv(path,keep_default_na=False)
    assert list(frame.shortcode)==['A','B']
    assert list(frame.new_field)==['','new']


def test_invalid_existing_survey_not_overwritten(tmp_path):
    path=tmp_path/'posts.csv'
    path.write_text('invalid',encoding='utf-8')
    with pytest.raises(RuntimeError,match='preservado'):
        append_survey(path,'posts',collection('A'))
    assert path.read_text()=='invalid'
