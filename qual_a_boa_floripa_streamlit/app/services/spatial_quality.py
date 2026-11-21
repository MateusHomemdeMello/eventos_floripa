import math
import re

from app.services.event_identity import normalized


AUTO = 'validado_automaticamente'
MANUAL = 'validado_manualmente'
REVIEW = 'revisao_pendente'
CONFLICT = 'conflito_fontes'
UNKNOWN = 'localizacao_desconhecida'
REOPENED = 'reaberto'
APPROVED = {AUTO, MANUAL}


def coordinate(value, limit):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and abs(value) <= limit else None


def has_position(row):
    return coordinate(row.get('latitude'), 90) is not None and coordinate(row.get('longitude'), 180) is not None


def address_parts(value):
    original = value if isinstance(value, str) else ''
    # A postal code is not a door number.
    original = re.sub(r'\b\d{5}[- ]?\d{3}\b', '', original)
    first = normalized(original.split(',')[0])
    parts = re.match(r'^(.*?)(?:\s+(?:n|no|numero))?\s+(\d+[a-z]?)$', first)
    if parts:
        return parts[1], parts[2]
    number = re.match(r'\s*(?:(?:n[º°o.]?|número)\s*)?(\d+[a-z]?)\s*(?:,|$)',
                      original.partition(',')[2], flags=re.I)
    return first, number[1].lower() if number else ''


def street_identity(value):
    text = normalized(value)
    return re.sub(r'^(?:avenida|av|rua|r|travessa|tv|estrada|est|rodovia|rod) ', '', text)


def organizer_as_venue(event):
    venue = normalized(event.get('local_informado'))
    organizer = normalized(event.get('responsavel_evento'))
    institution_names = {'universidade federal de santa catarina': 'ufsc'}
    venue = institution_names.get(venue, venue)
    organizer = institution_names.get(organizer, organizer)
    excerpt = normalized(event.get('evidencia_local') or event.get('descricao'))
    return bool(venue and venue == organizer and not re.search(
        rf'\b(?:na|no|em|local|onde) {re.escape(venue)}\b', excerpt.replace('universidade federal de santa catarina', 'ufsc')
    ))
