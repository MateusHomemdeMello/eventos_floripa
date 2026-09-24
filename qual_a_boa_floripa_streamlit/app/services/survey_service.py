"""Cumulative stage exports. Call while holding the PostHistory transaction lock."""
import csv
import hashlib
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.services.history_service import post_key, records

DATE_COLUMNS = ['data_scraping', 'data_analise_ia', 'data_geolocalizacao']
STAGE_FILES = {'posts': 'posts_coletados.csv', 'events': 'eventos_analisados.csv',
               'final': 'eventos_geolocalizados.csv'}


def stage_time():
    return datetime.now(ZoneInfo('America/Sao_Paulo')).isoformat(timespec='microseconds')


def with_dates(frame):
    frame = frame.copy()
    for name in DATE_COLUMNS:
        if name not in frame:
            frame[name] = None
    return frame


def append_survey(path: Path, stage: str, frame):
    """Append new observations logically, replacing atomically for schema evolution.

    Stable operation keys make startup/retry reconciliation idempotent. Existing
    observations are immutable, including when publication settings later change.
    Nested API payloads are encoded as JSON inside CSV cells.
    """
    columns = ['id_registro', 'id_coleta', *DATE_COLUMNS]
    old = []
    if path.exists():
        with path.open(encoding='utf-8-sig', newline='') as source:
            reader = csv.DictReader(source)
            if not reader.fieldnames or 'id_registro' not in reader.fieldnames:
                raise RuntimeError(f'CSV de levantamentos inválido: {path.name}. Arquivo preservado.')
            columns = list(reader.fieldnames)
            old = list(reader)
            if any(None in row or not row.get('id_registro') or any(v is None for v in row.values()) for row in old):
                raise RuntimeError(f'CSV de levantamentos inválido: {path.name}. Arquivo preservado.')
    known = {row['id_registro'] for row in old}
    new = []
    for row in records(with_dates(frame)):
        post_id = row.get('_post_id') or post_key(row)
        if not post_id:
            raise RuntimeError('Registro sem identificador de post; levantamento não foi gravado.')
        timestamp = row.get({'posts': 'data_scraping', 'events': 'data_analise_ia', 'final': 'data_geolocalizacao'}[stage])
        operation = row.get('id_coleta') if stage == 'posts' else timestamp
        identity = [stage, post_id, row.get('_event_index') if stage != 'posts' else None,
                    operation or timestamp or 'legado']
        key = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode('utf-8')).hexdigest()
        if key in known:
            continue
        known.add(key)
        row['id_registro'] = key
        row = {name: json.dumps(value, ensure_ascii=False, allow_nan=False) if isinstance(value, (dict, list))
               else '' if value is None else value for name, value in row.items()}
        new.append(row)
        columns.extend(name for name in row if name not in columns)
    if not new and path.exists():
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8-sig', newline='',
                                         dir=path.parent, delete=False) as target:
            temporary = Path(target.name)
            writer = csv.DictWriter(target, fieldnames=columns)
            writer.writeheader()
            writer.writerows(old)
            writer.writerows(new)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return len(new)
