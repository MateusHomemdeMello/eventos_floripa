from itertools import combinations
from hashlib import sha256
import json

import pandas as pd

from app.services.event_identity import (
    campus_pin_conflicts, campus_venue, distinctive_title, group_occurrences, normalized,
    publication_identity, same_occurrence,
)
from app.services.export_service import date_iso, hhmm, text_value
from app.services.spatial_quality import MANUAL, REOPENED


LOCATION_FIELDS = ('local_informado', 'endereco_informado', 'bairro_informado', 'referencia_local',
                   'municipio_informado', 'instituicao_local')


def _location_name(value):
    return normalized(value).replace('universidade federal de santa catarina', 'ufsc')


def _contains(container, contained):
    return f' {contained} ' in f' {container} '


def _compatible(left, right):
    return _contains(left, right) or _contains(right, left)


def _same_for_location(left, right):
    if same_occurrence(left, right):
        return True
    a, b = left['identity'], right['identity']
    if not distinctive_title(a['title']) or a['title'] != b['title']:
        return False
    if left['hora_inicio'] and right['hora_inicio'] and left['hora_inicio'] != right['hora_inicio']:
        return False
    if left['hora_fim'] and right['hora_fim'] and left['hora_fim'] != right['hora_fim']:
        return False
    if a['organizer'] and b['organizer'] and a['organizer'] != b['organizer']:
        return False
    if a['organizer'] and a['organizer'] == b['organizer']:
        return True
    for child, parent in ((left, right), (right, left)):
        if (child['venue'] and parent['venue'] and
                _location_name(child['event'].get('instituicao_local')) == _location_name(parent['venue'])):
            return True
    names = [_location_name(item['venue']) for item in (left, right)]
    return bool(left['hora_inicio'] and left['hora_inicio'] == right['hora_inicio']
                and all(names) and _compatible(*names))


def location_plan(history):
    events = [((key, str(index)), event) for key, entry in history.entries.items()
              for index, event in enumerate(entry['events'])]
    candidates = []
    groups = []
    for ref, event in events:
        saved = history.entries[ref[0]]['final'].get(ref[1])
        if saved and saved.get('correcao_localizacao'):
            event = {**event, **saved['correcao_localizacao']}
        start = date_iso(event.get('data_inicio'))
        venue = campus_venue(event) or text_value(event.get('local_informado'))
        candidate = {
            'ref': ref, 'event': event, 'venue': venue,
            'data_inicio': start, 'data_fim': date_iso(event.get('data_fim')) or start,
            'hora_inicio': hhmm(event.get('horario_inicio')), 'hora_fim': hhmm(event.get('horario_fim')),
            'identity': publication_identity(event, venue, event.get('endereco_informado'),
                                             event.get('bairro_informado'), ''),
            'reviewed': text_value(event.get('ocorrencia_revisada')),
        }
        candidate['saved'] = saved
        if start:
            candidates.append(candidate)
        else:
            groups.append([candidate])
    groups.extend(group_occurrences(candidates, matches=_same_for_location))

    queries, references, conflicts = [], [], []
    for group in groups:
        signature = sha256(json.dumps([
            [item['ref'], item['venue'], *(text_value(item['event'].get(field)) for field in LOCATION_FIELDS),
             text_value(item['event'].get('evidencia_local')), text_value(item['event'].get('evidencia_endereco'))]
            for item in group], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        refs = []
        for item in group:
            saved = item['saved']
            if saved is None:
                refs.append(item['ref'])
            elif saved.get('status_localizacao') != MANUAL and (
                saved.get('status_localizacao') == REOPENED or campus_pin_conflicts(item['event'], saved)
                or (saved.get('assinatura_localizacao') and saved['assinatura_localizacao'] != signature)
            ):
                refs.append(item['ref'])
        if not refs:
            continue
        primary = next((item for item in group if ':'.join(item['ref']) == item['reviewed']), group[0])
        query = dict(primary['event'])
        if any((item['saved'] or {}).get('status_localizacao') == REOPENED for item in group):
            query['_ignorar_cadastro'] = True
        links = list(dict.fromkeys(text_value(item['event'].get('url_post')) for item in group))
        links = [url for url in links if url]
        conflicting = []
        for field in LOCATION_FIELDS:
            values = [item['venue'] if field == 'local_informado' else text_value(item['event'].get(field))
                      for item in group]
            values = [value for value in values if value]
            if not values:
                continue
            if field != 'referencia_local' and any(
                not _compatible(a, b) for a, b in combinations(map(_location_name, values), 2)
            ):
                # A verified explicit parent permits an unnamed parent/child alias.
                if field == 'local_informado':
                    specific = [item for item in group if all(
                        _compatible(_location_name(item['venue']), _location_name(other['venue']))
                        or (_location_name(item['event'].get('instituicao_local')) and
                            _location_name(item['event'].get('instituicao_local')) == _location_name(other['venue']))
                        for other in group if other['venue'])]
                    if len(specific) == 1:
                        query[field] = specific[0]['venue']
                        continue
                conflicting.append(field)
            else:
                query[field] = max(values, key=lambda value: len(_location_name(value).split()))
        if conflicting:
            refs = [item['ref'] for item in group if not item['saved']
                    or item['saved'].get('status_localizacao') != MANUAL]
            conflicts.append({'url_post': query.get('url_post'), 'urls_publicacoes': links,
                              'evidencias_fontes': [
                                  {field: item['event'].get(field) for field in (*LOCATION_FIELDS, 'url_post',
                                   'evidencia_local', 'evidencia_endereco',
                                   'origem_evidencia_local', 'origem_evidencia_endereco')}
                                  for item in group
                              ],
                              'refs': refs, 'signature': signature,
                              'erro': 'Fontes divergem sobre o local. Revisão necessária: ' + ', '.join(conflicting)})
            continue
        queries.append(query)
        references.append((refs, links, signature))
    return pd.DataFrame(queries), references, pd.DataFrame(conflicts)
