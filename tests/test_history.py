from datetime import date
from pathlib import Path
from unittest.mock import patch
import pandas as pd
import pytest
from app.controllers.pipeline_controller import PipelineController
from app.models.config import AppConfig, Secrets
from app.services.history_service import PostHistory, post_key
from app.services.export_service import events_for_webgis, render_webgis
from app.services.geocoding_service import HereAuthenticationError


def posts(*keys):
    return pd.DataFrame([{'shortcode':key,'url_post':f'https://www.instagram.com/p/{key}/','legenda':'Show'} for key in keys])


def extract(_, frame, *args):
    return pd.DataFrame([{'shortcode':p.shortcode,'url_post':p.url_post,'evento':'Show '+p.shortcode,'data_inicio':'2099-01-01'} for p in frame.itertuples()]), pd.DataFrame()


def geocode(frame, *args):
    return frame.assign(latitude=-27.6,longitude=-48.5), pd.DataFrame()


@pytest.fixture
def controller(tmp_path):
    (tmp_path/'assets').mkdir()
    (tmp_path/'assets/webgis_template.html').write_text('/* EVENTOS_INICIO */ let eventos = []; /* EVENTOS_FIM */',encoding='utf-8')
    return PipelineController(AppConfig(),Secrets('a','b','c'),tmp_path)


def test_only_new_posts_use_apis_and_history_is_accumulated(controller):
    with patch('app.controllers.pipeline_controller.collect_posts',return_value=posts('A')) as collect, patch('app.controllers.pipeline_controller.extract_events',side_effect=extract) as ai, patch('app.controllers.pipeline_controller.geocode_events',side_effect=geocode) as here:
        controller.run()
        controller.run()
        assert ai.call_count==here.call_count==1
        collect.return_value=posts('B')
        result=controller.run()
        assert ai.call_count==here.call_count==2
        assert list(ai.call_args.args[1].shortcode)==['B']
        assert set(result.final.shortcode)=={'A','B'}
        assert result.webgis_path.read_text(encoding='utf-8').count('Show ')==2
        saved=PostHistory(controller.root/'data/banco_posts.csv')
        assert len(saved.entries)==2
        assert len(saved.frames()[2])==2


def test_here_failure_resumes_without_gpt(controller):
    with patch('app.controllers.pipeline_controller.collect_posts',return_value=posts('A')), patch('app.controllers.pipeline_controller.extract_events',side_effect=extract) as ai, patch('app.controllers.pipeline_controller.geocode_events',side_effect=HereAuthenticationError('401')) as here:
        with pytest.raises(HereAuthenticationError): controller.run()
        here.side_effect=geocode
        controller.run()
        assert ai.call_count==1
        assert here.call_count==2


def test_no_events_are_cached_but_extraction_failures_retry(controller):
    with patch('app.controllers.pipeline_controller.collect_posts',return_value=posts('A','B')), patch('app.controllers.pipeline_controller.extract_events',return_value=(pd.DataFrame(),pd.DataFrame([{'indice_post':1,'url_post':'https://www.instagram.com/p/B/'}]))) as ai, patch('app.controllers.pipeline_controller.geocode_events') as here:
        controller.run()
        ai.return_value=(pd.DataFrame(),pd.DataFrame())
        controller.run()
        assert list(ai.call_args.args[1].shortcode)==['B']
        here.assert_not_called()


def test_partial_here_failure_retries_only_failed_event(controller):
    def partial(frame,*args):
        return frame.assign(latitude=[-27.6,None],longitude=[-48.5,None]), pd.DataFrame([{'indice_evento':1}])
    with patch('app.controllers.pipeline_controller.collect_posts',return_value=posts('A','B')), patch('app.controllers.pipeline_controller.extract_events',side_effect=extract) as ai, patch('app.controllers.pipeline_controller.geocode_events',side_effect=partial) as here:
        controller.run()
        here.side_effect=geocode
        controller.run()
        assert ai.call_count==1
        assert list(here.call_args.args[0].shortcode)==['B']


def test_post_keys_ignore_tracking_and_route():
    assert post_key({'url_post':'https://www.instagram.com/reel/ABC/?igsh=123'})=='ABC'
    assert post_key({'url_post':'https://www.instagram.com/p/ABC/'})=='ABC'
    assert post_key({}) is None


def test_webgis_excludes_expired_but_keeps_today_and_multiday():
    frame=pd.DataFrame([
        {'evento':'old','data_inicio':'2026-09-01'},
        {'evento':'today','data_inicio':'2026-09-13'},
        {'evento':'ongoing','data_inicio':'2026-09-01','data_fim':'2026-09-14'},
        {'evento':'ends_today','data_inicio':'2026-09-01','data_fim':'2026-09-13'},
        {'evento':'future','data_inicio':'2026-09-20'},
        {'evento':'invalid','data_inicio':'invalid'},
        {'evento':'reversed','data_inicio':'2026-09-20','data_fim':'2026-09-10'},
    ]).assign(latitude=-27.6,longitude=-48.5)
    assert {r['nome'] for r in events_for_webgis(frame,date(2026,9,13))}=={'today','ongoing','ends_today','future'}
    assert len(frame)==7


def test_migration_preserves_gpt_and_retries_missing_coordinates(tmp_path):
    old=tmp_path/'old.csv'
    pd.DataFrame([{'url_post':'https://www.instagram.com/p/A/','evento':'Show','latitude':None,'longitude':None}]).to_csv(old,index=False)
    history=PostHistory(tmp_path/'banco.csv')
    history.migrate_export(old)
    restored=PostHistory(history.path)
    assert len(restored.pending()[0])==1
    assert 'latitude' not in restored.pending()[0].columns


def test_corrupt_csv_is_not_overwritten(tmp_path):
    path=tmp_path/'banco.csv'
    path.write_text('invalid',encoding='utf-8')
    with pytest.raises(RuntimeError,match='inválido'): PostHistory(path)
    assert path.read_text()=='invalid'


def test_transaction_rejects_parallel_writer(tmp_path):
    path=tmp_path/'banco.csv'
    with PostHistory.transaction(path):
        with pytest.raises(RuntimeError,match='Outro processamento'):
            with PostHistory.transaction(path): pass


def test_failed_replace_keeps_previous_csv(tmp_path):
    history=PostHistory(tmp_path/'banco.csv')
    history.add_extraction('A',{},[])
    history.save()
    before=history.path.read_bytes()
    history.add_extraction('B',{},[])
    with patch('app.services.history_service.os.replace',side_effect=OSError('disk failure')):
        with pytest.raises(OSError): history.save()
    assert history.path.read_bytes()==before
