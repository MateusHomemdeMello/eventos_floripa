"""Persistent post history. One CSV row per post, with JSON payloads for its events."""
import csv
import json
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd


def post_key(row):
    shortcode = row.get('shortcode')
    if isinstance(shortcode, str) and shortcode.strip():
        return shortcode.strip()
    url = row.get('url_post')
    match = re.search(r'instagram\.com/(?:p|reel|reels|tv)/([^/?#]+)', url or '') if isinstance(url, str) else None
    return match.group(1) if match else None


def records(frame):
    return json.loads(frame.to_json(orient='records', date_format='iso'))


def json_default(value):
    """Encode dates carried by pandas rows without discarding timezone/precision."""
    if value is pd.NaT or value is pd.NA:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(f'Object of type {type(value).__name__} is not JSON serializable')


class PostHistory:
    columns = ['post_id', 'status', 'updated_at', 'post_json', 'events_json', 'final_json']

    def __init__(self, path: Path):
        self.path = path
        self.entries = {}
        if path.exists():
            try:
                with path.open(encoding='utf-8-sig', newline='') as source:
                    reader = csv.DictReader(source)
                    if reader.fieldnames != self.columns:
                        raise ValueError('Cabeçalho inválido')
                    for row in reader:
                        key = row['post_id']
                        if not key or key in self.entries:
                            raise ValueError('Identificador inválido ou repetido')
                        post, events, final = (json.loads(row[name]) for name in ['post_json', 'events_json', 'final_json'])
                        if not isinstance(post, dict) or not isinstance(events, list) or not isinstance(final, dict):
                            raise ValueError('Dados inválidos')
                        if not all(isinstance(event, dict) for event in events) or not all(isinstance(event, dict) for event in final.values()):
                            raise ValueError('Eventos inválidos')
                        if not set(final).issubset({str(i) for i in range(len(events))}):
                            raise ValueError('Índices inválidos')
                        self.entries[key] = {'post': post, 'events': events, 'final': final, 'updated_at': row['updated_at']}
            except (ValueError, KeyError, TypeError, csv.Error):
                raise RuntimeError('O banco CSV está inválido. Restaure uma cópia válida antes de processar; o arquivo foi preservado.') from None

    @classmethod
    @contextmanager
    def transaction(cls, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        # OS lock is released even if the process crashes; the lock file may remain.
        with path.with_suffix('.lock').open('a+b') as lock:
            lock.seek(0, os.SEEK_END)
            if lock.tell() == 0:
                lock.write(b'0'); lock.flush()
            lock.seek(0)
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise RuntimeError('Outro processamento está atualizando o banco. Aguarde sua conclusão.') from None
            try:
                yield cls(path)
            finally:
                lock.seek(0)
                if os.name == 'nt': msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                else: fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def add_extraction(self, key, post, events):
        self.entries[key] = {'post': post, 'events': events, 'final': {}, 'updated_at': datetime.now(timezone.utc).isoformat()}

    def set_publication(self, post_id, event_index, publish):
        entry=self.entries.get(str(post_id))
        index=str(event_index)
        if entry is None or not index.isdigit() or int(index) >= len(entry['events']):
            raise KeyError('Evento não encontrado no banco.')
        value=bool(publish)
        entry['events'][int(index)]['publicar_webgis']=value
        if index in entry['final']:
            entry['final'][index]['publicar_webgis']=value
        entry['updated_at']=datetime.now(timezone.utc).isoformat()

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8-sig', newline='', dir=self.path.parent, delete=False) as target:
                temporary = Path(target.name)
                writer = csv.DictWriter(target, fieldnames=self.columns)
                writer.writeheader()
                for key, entry in self.entries.items():
                    status = 'concluido' if len(entry['final']) == len(entry['events']) else 'here_pendente'
                    writer.writerow({'post_id': key, 'status': status, 'updated_at': entry['updated_at'],
                                     **{name+'_json': json.dumps(entry[source], ensure_ascii=False, allow_nan=False, default=json_default)
                                        for name, source in [('post','post'), ('events','events'), ('final','final')]}})
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None and temporary.exists(): temporary.unlink()

    def frames(self):
        from app.services.survey_service import with_dates
        posts, events, final = [], [], []
        for key,entry in self.entries.items():
            posts.append(entry['post'])
            images=entry['post'].get('imagens') or []
            post_photo=images[0] if isinstance(images,list) and images and isinstance(images[0],str) else None
            for index,event in enumerate(entry['events']):
                events.append({**event, '_post_id': key, '_event_index': index})
            for index, event in enumerate(entry['events']):
                row = dict(entry['final'].get(str(index), event))
                row['foto_url']=row.get('foto_url') or event.get('foto_url') or post_photo
                row['publicar_webgis']=bool(row.get('publicar_webgis',event.get('publicar_webgis',True)))
                row['_post_id']=key
                row['_event_index']=index
                if str(index) not in entry['final']:
                    row.update(necessita_revisao=True, motivo_revisao='Localização pendente')
                final.append(row)
        output = pd.DataFrame(final)
        output['id'] = range(1, len(output)+1)
        return with_dates(pd.DataFrame(posts)), with_dates(pd.DataFrame(events)), with_dates(output)

    def pending(self):
        rows, references = [], []
        for key, entry in self.entries.items():
            for index, event in enumerate(entry['events']):
                if str(index) not in entry['final']:
                    rows.append(event); references.append((key, str(index)))
        return pd.DataFrame(rows), references

    def migrate_export(self, path):
        """Recover extracted events from the old output, but never infer no-event posts."""
        if self.path.exists() or not path.exists(): return
        try:
            frame = pd.read_csv(path)
        except pd.errors.EmptyDataError:
            return
        if not {'url_post', 'evento'}.issubset(frame.columns): return
        for row in records(frame):
            key = post_key(row)
            if not key or not row.get('evento'): continue
            if key not in self.entries:
                self.add_extraction(key, {name: row.get(name) for name in ['url_post','shortcode','perfil','data_publicacao']}, [])
            entry = self.entries[key]
            index = str(len(entry['events']))
            extraction_fields = ['evento','categoria','data_inicio','data_fim','horario_inicio','horario_fim',
                                 'local_informado','endereco_informado','bairro_informado','referencia_local',
                                 'descricao','confianca_extracao','observacoes','perfil','data_publicacao',
                                 'url_post','shortcode','foto_url','erros_imagem']
            entry['events'].append({name: row.get(name) for name in extraction_fields})
            if pd.notna(row.get('latitude')) and pd.notna(row.get('longitude')):
                entry['final'][index] = row
        if self.entries: self.save()
