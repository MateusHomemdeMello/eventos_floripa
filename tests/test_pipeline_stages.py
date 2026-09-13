from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from app.controllers.pipeline_controller import PipelineController
from app.models.config import AppConfig, Secrets


def test_stages_published_before_next_service_and_preserved_on_failure(tmp_path):
    snapshots = {}
    posts = pd.DataFrame({'legenda': ['Show'], 'shortcode':['ABC'], 'url_post':['https://www.instagram.com/p/ABC/']})
    events = pd.DataFrame({'evento': ['Show'], 'shortcode':['ABC'], 'url_post':['https://www.instagram.com/p/ABC/']})

    def extract(*args):
        assert snapshots['posts'].drop(columns='ja_analisado').equals(posts)
        return events, pd.DataFrame()

    def locate(*args):
        assert snapshots['events'].equals(events)
        events.loc[0, 'evento'] = 'changed'
        raise RuntimeError('HERE unavailable')

    with patch('app.controllers.pipeline_controller.collect_posts', return_value=posts), patch('app.controllers.pipeline_controller.extract_events', side_effect=extract), patch('app.controllers.pipeline_controller.geocode_events', side_effect=locate):
        controller = PipelineController(AppConfig(), Secrets('', '', ''), tmp_path)
        with pytest.raises(RuntimeError, match='HERE unavailable'):
            controller.run(on_stage=lambda name, frame: snapshots.update({name: frame}))
    assert list(snapshots) == ['posts', 'events']
    assert snapshots['events'].iloc[0]['evento'] == 'Show'
