from unittest.mock import Mock, patch
import pytest
import requests
from app.models.config import AppConfig
from app.services.config_service import parse_config, export_config
from app.services.geocoding_service import request_here, HereAuthenticationError


def test_config_roundtrip():
    config=AppConfig(days_back=30,reference_city='São José',use_web_fallback=False)
    assert parse_config(export_config(config)) == config
    assert 'api_key' not in export_config(config)


@pytest.mark.parametrize('text',['[]','{','{"days_back":0}','{"days_back":true}','{"profiles":"abc"}','{"reference_lat":91}','{"max_radius_km":NaN}','{"unknown":1}'])
def test_invalid_config(text):
    with pytest.raises(ValueError): parse_config(text)


def test_here_auth_does_not_expose_key():
    response=Mock(status_code=401)
    with patch('app.services.geocoding_service.requests.get',return_value=response):
        with pytest.raises(HereAuthenticationError) as error:
            request_here('https://example.invalid',{},'private-key')
    assert 'private-key' not in str(error.value)
    assert '401' in str(error.value)


def test_here_network_error_does_not_expose_url():
    with patch('app.services.geocoding_service.requests.get',side_effect=requests.ConnectionError('url?apiKey=private-key')):
        with pytest.raises(RuntimeError) as error:
            request_here('https://example.invalid',{},'private-key')
    assert 'private-key' not in str(error.value)
