from datetime import datetime, timezone
from uuid import uuid4

from app.services.event_identity import normalized
from app.services.geocoding_service import haversine_km
from app.services.spatial_quality import APPROVED, AUTO, address_parts, coordinate, has_position, organizer_as_venue, street_identity


def lookup_venue(event, venues, config):
    if str(event.get('_ignorar_cadastro')).lower() == 'true':
        return None
    name = normalized(event.get('local_informado'))
    if not name or organizer_as_venue(event):
        return None
    address, number = address_parts(event.get('endereco_informado'))
    city = normalized(event.get('municipio_informado'))
    district = normalized(event.get('bairro_informado'))
    parent = normalized(event.get('instituicao_local'))
    allowed = {normalized(config.reference_city), *(normalized(c) for c in config.allowed_municipalities)}
    matches = []
    for venue in venues:
        point = venue['location']
        if not venue['active'] or point.get('status_localizacao') not in APPROVED or not has_position(point):
            continue
        if haversine_km(config.reference_lat, config.reference_lon, coordinate(point['latitude'], 90),
                        coordinate(point['longitude'], 180)) > config.max_radius_km:
            continue
        if point.get('precisao_localizacao') not in {'poi', 'manual'}:
            continue
        if name not in {normalized(venue['name']), *(normalized(alias) for alias in venue['aliases'])}:
            continue
        stored_city = normalized(point.get('municipio'))
        if stored_city and (stored_city not in allowed or (city and city != stored_city)):
            continue
        if city and not stored_city:
            continue
        if district and district != normalized(point.get('bairro')):
            continue
        if parent and parent != normalized(venue.get('parent')):
            continue
        street, stored_number = address_parts(point.get('endereco'))
        if address and street_identity(address) != street_identity(street):
            continue
        if number and number != stored_number:
            continue
        matches.append(venue)
    if len(matches) != 1:
        return None
    venue = matches[0]
    return {**venue['location'], 'local_cadastrado_id': venue['id'],
            'origem_localizacao': 'cadastro_locais', 'status_localizacao': AUTO}


def registry_entry(location, *, aliases=(), parent='', previous=None):
    stamp = datetime.now(timezone.utc).isoformat()
    return {
        'id': previous['id'] if previous else str(uuid4()),
        'name': location['local_padronizado'],
        'aliases': sorted({alias.strip() for alias in (*aliases, *(previous['aliases'] if previous else []))
                           if isinstance(alias, str) and alias.strip()}),
        'parent': parent or (previous.get('parent', '') if previous else ''),
        'location': location, 'active': True, 'verified_at': stamp,
        'history': previous['history'] + [{'at': stamp, 'previous': previous['location']}] if previous else [],
    }
