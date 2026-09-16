from pathlib import Path
from unittest.mock import patch
from streamlit.testing.v1 import AppTest
from app.services.history_service import PostHistory


def test_startup_restores_history_without_external_apis(tmp_path):
    (tmp_path/'assets').mkdir()
    (tmp_path/'assets/webgis_template.html').write_text('/* EVENTOS_INICIO */ let eventos = []; /* EVENTOS_FIM */',encoding='utf-8')
    history=PostHistory(tmp_path/'data/banco_posts.csv')
    event={'evento':'Saved event','url_post':'https://www.instagram.com/p/A/','data_inicio':'2099-01-01'}
    history.add_extraction('A',{'url_post':event['url_post']},[event])
    history.entries['A']['final']['0']={**event,'latitude':-27.6,'longitude':-48.5}
    history.save()
    script=f'from pathlib import Path\nfrom app.views.streamlit_view import render\nrender(Path({str(tmp_path)!r}))'
    with patch('app.controllers.pipeline_controller.collect_posts') as scraper, patch('app.controllers.pipeline_controller.extract_events') as ai, patch('app.controllers.pipeline_controller.geocode_events') as here:
        app=AppTest.from_string(script).run(timeout=30)
        assert not app.exception
        assert len(app.dataframe)==4
        assert app.session_state['result'].final.iloc[0]['evento']=='Saved event'
        scraper.assert_not_called(); ai.assert_not_called(); here.assert_not_called()
        app.text_area(key='config_text').set_value('{"OPENAI_API_KEY":"test-openai","APIFY_API_TOKEN":"test-apify","HERE_API_KEY":"test-here"}')
        next(button for button in app.button if button.label=='Aplicar JSON').click().run()
        assert not app.exception
        assert app.text_input(key='credential_OPENAI_API_KEY').value=='test-openai'
        assert app.text_input(key='credential_APIFY_API_TOKEN').value=='test-apify'
        assert app.text_input(key='credential_HERE_API_KEY').value=='test-here'
        app.text_area(key='config_text').set_value('{"days_back":21}')
        next(button for button in app.button if button.label=='Aplicar JSON').click().run()
        assert app.text_input(key='credential_HERE_API_KEY').value=='test-here'
