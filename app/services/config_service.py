import json
import math
from dataclasses import asdict
from app.models.config import AppConfig


def parse_config(text):
    try:
        data = json.loads(text)
    except (ValueError, UnicodeError):
        raise ValueError('JSON inválido. Confira aspas, vírgulas e chaves.') from None
    if not isinstance(data, dict):
        raise ValueError('A configuração deve ser um objeto JSON.')
    defaults = asdict(AppConfig())
    unknown = set(data) - set(defaults)
    if unknown:
        raise ValueError('O JSON contém campos desconhecidos. Use o arquivo exportado como modelo.')
    values = {**defaults, **data}
    bounds = {'days_back': (1,90), 'results_limit': (1,200), 'max_radius_km': (1,500),
              'reference_lat': (-90,90), 'reference_lon': (-180,180), 'jpeg_quality': (1,100),
              'min_location_confidence': (0,1), 'min_candidate_difference': (0,1)}
    for key, value in values.items():
        default = defaults[key]
        if isinstance(default, bool):
            valid = type(value) is bool
        elif isinstance(default, (int,float)):
            valid = type(value) in (int,float) and math.isfinite(value)
            if type(default) is int: valid = valid and type(value) is int
            low, high = bounds.get(key, (0 if key == 'ai_interval_seconds' else 1, float('inf')))
            valid = valid and low <= value <= high
        elif isinstance(default, list):
            valid = isinstance(value,list) and bool(value) and all(isinstance(x,str) and x.strip() for x in value)
        else:
            valid = isinstance(value,str) and bool(value.strip())
        if not valid:
            raise ValueError(f'Valor inválido para {key}.')
        if type(default) is float: values[key]=float(value)
    return AppConfig(**values)


def export_config(config):
    return json.dumps(asdict(config), ensure_ascii=False, indent=2, allow_nan=False)
