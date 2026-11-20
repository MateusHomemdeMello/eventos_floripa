from itertools import combinations

import pandas as pd

from app.services.event_identity import campus_pin_conflicts, campus_venue, group_occurrences, normalized, publication_identity, same_occurrence
from app.services.export_service import analysis_day, date_iso, hhmm, text_value


LOCATION_FIELDS = ('local_informado', 'endereco_informado', 'bairro_informado', 'referencia_local')


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
    if not a['title'] or a['title'] != b['title']:
        return False
    if left['hora_inicio'] and right['hora_inicio'] and left['hora_inicio'] != right['hora_inicio']:
        return False
    if left['hora_fim'] and right['hora_fim'] and left['hora_fim'] != right['hora_fim']:
        return False
    if a['organizer'] and b['organizer'] and a['organizer'] != b['organizer']:
        return False
    if a['organizer'] and a['organizer'] == b['organizer']:
        return True
    names = [_location_name(item['venue']) for item in (left, right)]
    return bool(left['hora_inicio'] and left['hora_inicio'] == right['hora_inicio']
                and all(names) and _compatible(*names))


def latest_location_plan(history):
    dated = [(analysis_day(event.get('data_analise_ia')), (key, str(index)), event)
             for key, entry in history.entries.items() for index, event in enumerate(entry['events'])]
    latest = max((day for day, _, _ in dated if day), default=None)
    candidates = []
    groups = []
    for day, ref, event in dated:
        if day != latest:
            continue
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
        saved = history.entries[ref[0]]['final'].get(ref[1])
        candidate['pending'] = saved is None or campus_pin_conflicts(event, saved)
        if start:
            candidates.append(candidate)
        else:
            groups.append([candidate])
    groups.extend(group_occurrences(candidates, matches=_same_for_location))

    queries, references, conflicts = [], [], []
    for group in groups:
        refs = [item['ref'] for item in group if item['pending']]
        if not refs:
            continue
        primary = next((item for item in group if ':'.join(item['ref']) == item['reviewed']), group[0])
        query = dict(primary['event'])
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
                conflicting.append(field)
            else:
                query[field] = max(values, key=lambda value: len(_location_name(value).split()))
        if conflicting:
            conflicts.append({'url_post': query.get('url_post'), 'urls_publicacoes': links,
                              'erro': 'Fontes divergem sobre o local. Revisão necessária: ' + ', '.join(conflicting)})
            continue
        queries.append(query)
        references.append((refs, links))
    return pd.DataFrame(queries), references, pd.DataFrame(conflicts), latest
