import re
import unicodedata
from collections import defaultdict
from difflib import SequenceMatcher


def normalized(value):
    if not isinstance(value, str):
        return ''
    folded = unicodedata.normalize('NFKD', value.casefold())
    return ' '.join(re.findall(r'[a-z0-9]+', ''.join(c for c in folded if not unicodedata.combining(c))))


def outside_agenda(title):
    name = normalized(title)
    return bool(re.match(
        r'^(?:(?:abertura|prazo|periodo|inicio|fim|encerramento) (?:de|das|para) )?'
        r'(?:matriculas?|renovacao de matricula|recadastramento)\b', name
    ) or re.search(r'\b(?:rematriculas?|marcha para jesus)\b|'
                   r'^(?:culto|missa|vigilia religiosa|celebracao liturgica)\b', name))


def campus_venue(row):
    unit = r'(?:cce|centro de comunicacao e expressao)'
    university = r'(?:ufsc|universidade federal de santa catarina)'
    campus = rf'\b{unit}(?: (?:da|na|campus|ufsc))* {university}\b'
    local = normalized(row.get('local_informado'))
    description = normalized(row.get('descricao'))
    local_matches = re.search(rf'\b{unit}\b', local) and re.search(rf'\b{university}\b', local)
    if local_matches or re.search(rf'(?:^|\b(?:no|na|local|em) ){campus}', description):
        return 'CCE da UFSC'
    if re.search(rf'\b{unit}\b', local) and re.search(rf'\b{university}\b', normalized(row.get('referencia_local'))):
        return 'CCE da UFSC'
    return ''


def matches_campus_venue(title, address):
    text = normalized(f'{title or ""} {address or ""}')
    return bool(re.search(r'\bcce\b|\bcentro de comunicacao e expressao\b', text))


def campus_pin_conflicts(event, location):
    return bool(location.get('latitude') is not None and location.get('longitude') is not None
                and campus_venue(event)
                and not matches_campus_venue(location.get('local_padronizado'), location.get('endereco')))


def _venue_name(value):
    name = normalized(value)
    return name if name not in {'local nao informado', 'local a confirmar', 'a definir', 'online'} else ''


def same_occurrence(a, b):
    if (a['data_inicio'], a['data_fim']) != (b['data_inicio'], b['data_fim']):
        return False
    for key in ('hora_inicio', 'hora_fim'):
        if a[key] and b[key] and a[key] != b[key]:
            return False
    left, right = a['identity'], b['identity']
    if left['organizer'] and right['organizer'] and left['organizer'] != right['organizer']:
        return False
    if left['here_id'] and right['here_id'] and left['here_id'] != right['here_id']:
        return False
    common_names = left['venues'] & right['venues']
    same_here = left['here_id'] and left['here_id'] == right['here_id']
    if not (common_names or same_here):
        return False
    if left['address'] and right['address'] and left['address'] != right['address'] and not same_here:
        # A differing known street number is strong evidence of another unit.
        if re.findall(r'\b\d+\b', left['address']) != re.findall(r'\b\d+\b', right['address']):
            return False
    if left['district'] and right['district'] and left['district'] != right['district'] and not same_here:
        return False

    first, second = left['title'], right['title']
    same_organizer = left['organizer'] and left['organizer'] == right['organizer']
    same_time = a['hora_inicio'] and a['hora_inicio'] == b['hora_inicio']
    meaningful = {'de', 'do', 'da', 'das', 'dos', 'em', 'e', 'com', 'o', 'a', 'show', 'oficina', 'evento'}
    if first and first == second:
        return bool(same_organizer or same_time or set(first.split()) - meaningful)
    if not same_organizer or not (first and second):
        return False
    first_words, second_words = set(first.split()) - meaningful, set(second.split()) - meaningful
    return bool(len(first_words & second_words) >= 2 and
                re.findall(r'\b\d+\b', first) == re.findall(r'\b\d+\b', second) and
                SequenceMatcher(None, first, second).ratio() >= 0.90)


def publication_identity(row, local, address, district, here_id):
    venues = {_venue_name(local), _venue_name(row.get('local_informado'))} - {''}
    return {
        'venues': venues,
        'title': normalized(row.get('evento')),
        'organizer': normalized(row.get('responsavel_evento')),
        'here_id': here_id,
        'address': normalized(address),
        'district': normalized(district),
    }


def group_occurrences(events, *, by_analysis_day=False, matches=same_occurrence):
    def day(event):
        return event.get('analysis_day') if by_analysis_day else None

    def dates(event):
        return day(event), event['data_inicio'], event['data_fim']

    reviewed_groups = defaultdict(list)
    buckets = defaultdict(list)
    for event in events:
        if event.get('reviewed'):
            reviewed_groups[(day(event), event['reviewed'])].append(event)
    for group in reviewed_groups.values():
        for key in {dates(event) for event in group}:
            buckets[key].append(group)

    groups = []
    displayed = set()
    for event in events:
        reviewed = event.get('reviewed')
        if reviewed:
            group = reviewed_groups[(day(event), reviewed)]
        else:
            key = dates(event)
            for group in buckets[key]:
                if all(matches(event, member) for member in group):
                    group.append(event)
                    break
            else:
                group = [event]
                buckets[key].append(group)
        if id(group) not in displayed:
            displayed.add(id(group))
            groups.append(group)
    return groups
