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
    def partial(frame,key,openai,config,progress,on_result,resolve_cached=None):
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


def test_collection_append_failure_recovered_before_next_collection(controller):
    fail_once=True
    def interrupted(path,stage,frame):
        nonlocal fail_once
        if stage=='posts' and not frame.empty and fail_once:
            fail_once=False
            raise OSError('CSV temporarily locked')
        return append_survey(path,stage,frame)
    with patch('app.controllers.pipeline_controller.collect_posts',return_value=collection('A')) as scraper, patch('app.controllers.pipeline_controller.append_survey',side_effect=interrupted):
        controller.collect_stage()
        assert any('CSV temporarily locked' in warning for warning in controller.output_warnings)
        scraper.return_value=collection('B')
        controller.collect_stage()
    assert list(read(controller,'posts').shortcode)==['A','B']


def test_locked_csvs_and_interruption_do_not_repeat_paid_extraction(controller, monkeypatch):
    import os

    original_replace = os.replace
    locked = {controller.history_path, controller.survey_paths['events']}

    def replace(source, target):
        if Path(target) in locked:
            raise PermissionError('CSV aberto no Excel')
        return original_replace(source, target)

    def interrupted(_, frame, config, progress, persist):
        post = frame.iloc[0]
        persist(0, post, [{'evento': 'Show A', 'shortcode': 'A'}], None)
        raise RuntimeError('interrupted')

    with patch('app.controllers.pipeline_controller.collect_posts', return_value=collection('A', 'B')) as scraper, patch('app.controllers.pipeline_controller.extract_events', side_effect=interrupted) as ai, patch('app.controllers.pipeline_controller.geocode_events', side_effect=locate):
        controller.collect_stage()
        first_csv = controller.survey_paths['events'].read_bytes()
        monkeypatch.setattr(os, 'replace', replace)
        with pytest.raises(RuntimeError, match='interrupted'):
            controller.extraction_stage()
        assert controller.survey_paths['events'].read_bytes() == first_csv
        restarted = PipelineController(controller.config, controller.secrets, controller.root)
        stages, _ = restarted.restore_state()
        assert list(stages['events'].shortcode) == ['A']
        assert any('CSV aberto no Excel' in warning for warning in restarted.output_warnings)
        locked.clear()
        ai.side_effect = extract
        result = restarted.run(resume=True)
        assert list(ai.call_args.args[1].shortcode) == ['B']
        assert scraper.call_count == 1
        assert set(result.final.shortcode) == {'A', 'B'}
        assert set(read(restarted, 'events').shortcode) == {'A', 'B'}
        restarted.history_path.unlink()
        restarted.restore_state()
        assert set(pd.read_csv(restarted.history_path).post_id) == {'A', 'B'}


def test_new_collection_keeps_unprocessed_posts_from_previous_batch(controller):
    with patch('app.controllers.pipeline_controller.collect_posts', side_effect=[collection('A'), collection('B')]), patch('app.controllers.pipeline_controller.extract_events', side_effect=extract) as ai:
        controller.collect_stage()
        controller.collect_stage()
        controller.extraction_stage()
    assert set(ai.call_args.args[1].shortcode) == {'A', 'B'}


def test_apify_download_retry_reuses_the_saved_run(controller):
    from types import SimpleNamespace
    from unittest.mock import Mock

    client = Mock()
    client.actor.return_value.start.return_value = SimpleNamespace(id='run-1')
    client.run.return_value.wait_for_finish.return_value = SimpleNamespace(
        status='SUCCEEDED', default_dataset_id='dataset-1', started_at='2026-09-24T12:00:00Z')
    client.dataset.return_value.iterate_items.side_effect = [
        OSError('network interrupted'),
        iter([{'shortCode': 'A', 'timestamp': '2026-09-24T10:00:00Z'}]),
    ]
    with patch('app.services.instagram_service.ApifyClient', return_value=client):
        with pytest.raises(OSError, match='network interrupted'):
            controller.collect_stage()
        assert controller.apify_run_path.exists()
        restarted = PipelineController(controller.config, controller.secrets, controller.root)
        assert list(restarted.collect_stage().shortcode) == ['A']
    client.actor.return_value.start.assert_called_once()
    assert [call.args for call in client.run.call_args_list] == [('run-1',), ('run-1',)]
    assert not restarted.apify_run_path.exists()
