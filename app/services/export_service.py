import json, math, re
from pathlib import Path
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo
from app.services.event_identity import campus_venue, group_occurrences, matches_campus_venue, outside_agenda, publication_identity
from app.services.spatial_quality import APPROVED, CONFLICT, MANUAL

def text_value(value, default=""):
    if value is None or not pd.api.types.is_scalar(value): return default
    try:
        if pd.isna(value): return default
    except Exception: pass
    text=str(value).strip(); return text if text else default

def date_iso(value):
    text=text_value(value)
    if not text: return ""
    date=pd.to_datetime(text,errors="coerce",dayfirst=False)
    return "" if pd.isna(date) else date.strftime("%Y-%m-%d")

def hhmm(value):
    text=text_value(value).lower()
    m=re.fullmatch(r"([01]?\d|2[0-3])(?:\s*:\s*([0-5]\d)|\s*h\s*([0-5]\d)|\s*(?:h|horas?))?",text)
    return "" if not m else f"{int(m.group(1)):02d}:{int(m.group(2) or m.group(3) or 0):02d}"

def finite(value):
    try: n=float(value)
    except (TypeError,ValueError): return None
    return n if math.isfinite(n) else None

CATEGORIES = {
    "exposicoes", "apresentacoes", "musica", "feiras", "esportes",
    "gastronomia", "cinema_audiovisual", "cursos_oficinas", "festivais", "encontros",
}

def normalized_category(value, event_name="", description=""):
    category=text_value(value).lower().strip().replace(" ", "_")
    aliases={"feira":"feiras", "esporte":"esportes", "cinema":"cinema_audiovisual", "oficina":"cursos_oficinas"}
    category=aliases.get(category,category)
    if category in CATEGORIES: return category
    # Registros antigos usavam a categoria ampla "cultura". Termos
    # inequívocos permitem exibi-los de forma útil na nova taxonomia.
    text=f"{text_value(event_name)} {text_value(description)}".lower()
    rules=(
        ("cursos_oficinas", ("oficina", "curso", "capacita", "workshop")),
        ("cinema_audiovisual", ("cinema", "cineclube", "filme", "audiovisual", "curta-metragem")),
        ("exposicoes", ("exposição", "exposicao", "mostra de arte", "instalação", "instalacao")),
        ("apresentacoes", ("teatro", "teatral", "dança", "danca", "circo", "stand-up", "recital")),
        ("festivais", ("festival",)),
        ("encontros", ("palestra", "seminário", "seminario", "debate", "roda de conversa", "congresso", "encontro")),
    )
    return next((name for name,terms in rules if any(term in text for term in terms)), "encontros")


def analysis_day(value):
    stamp = pd.to_datetime(value, errors='coerce')
    if pd.isna(stamp):
        return None
    return (stamp.tz_convert('America/Sao_Paulo') if stamp.tzinfo else stamp).date()


def consolidate_extractions(frame):
    """Validate the extracted table by analysis day in São Paulo."""
    candidates = []
    for ordinal, (_, row) in enumerate(frame.iterrows()):
        day = analysis_day(row.get('data_analise_ia'))
        start = date_iso(row.get('data_inicio'))
        candidates.append({
            'row': row.to_dict(), 'ordinal': ordinal, 'analysis_day': day, 'data_inicio': start,
            'data_fim': date_iso(row.get('data_fim')) or start,
            'hora_inicio': hhmm(row.get('horario_inicio')), 'hora_fim': hhmm(row.get('horario_fim')),
            'identity': publication_identity(row, campus_venue(row) or row.get('local_informado'),
                                             row.get('endereco_informado'), row.get('bairro_informado'), ''),
            'instagram_url': text_value(row.get('url_post')),
            'reviewed': text_value(row.get('ocorrencia_revisada')),
            'preferred': text_value(row.get('ocorrencia_revisada')) == occurrence_id(row),
        })
    dated = [event for event in candidates if event['analysis_day'] and event['data_inicio']]
    groups = group_occurrences(dated, by_analysis_day=True)
    groups.extend([event] for event in candidates if not event['analysis_day'] or not event['data_inicio'])
    groups.sort(key=lambda group: group[0]['ordinal'])
    return pd.DataFrame([
        {**max(group, key=lambda event: event['preferred'])['row'],
         'urls_publicacoes': source_links(group)} for group in groups
    ], columns=[*frame.columns, 'urls_publicacoes'])


def source_links(group):
    return list(dict.fromkeys(url for event in group
                             for url in [event['instagram_url'], *event.get('location_sources', [])] if url))


def occurrence_id(row):
    post = text_value(row.get('_post_id'))
    return f"{post}:{text_value(row.get('_event_index'), '0')}" if post else None
def events_for_webgis(df, today=None):
    reference = today or datetime.now(ZoneInfo('America/Sao_Paulo'))
    if isinstance(reference, datetime):
        reference = reference.astimezone(ZoneInfo('America/Sao_Paulo')) if reference.tzinfo else reference.replace(tzinfo=ZoneInfo('America/Sao_Paulo'))
    today = reference.date() if isinstance(reference, datetime) else reference
    candidates = []
    for ordinal, (_, r) in enumerate(df.iterrows(), start=1):
        publish=r.get("publicar_webgis",True)
        if isinstance(publish,str):
            if publish.strip().lower() in {"false","0","não","nao"}: continue
        elif not pd.isna(publish) and not bool(publish): continue
        if outside_agenda(r.get('evento')): continue
        location_status = text_value(r.get('status_localizacao'))
        if location_status == CONFLICT: continue
        lat,lng=finite(r.get("latitude")),finite(r.get("longitude")); start=date_iso(r.get("data_inicio"))
        if not start: continue
        if location_status not in APPROVED or lat is None or lng is None or not(-90<=lat<=90) or not(-180<=lng<=180):
            lat,lng=None,None
        end=date_iso(r.get('data_fim')) or start
        if end < start or end < today.isoformat(): continue
        cat=normalized_category(r.get("categoria"),r.get("evento"),r.get("descricao"))
        try: eid=int(r.get("id"))
        except (TypeError, ValueError, OverflowError): eid=ordinal
        # History identity is stable even if rows are reordered between exports.
        eid = occurrence_id(r) or eid
        h1=hhmm(r.get("horario_inicio")); h2=hhmm(r.get("horario_fim"))
        if isinstance(reference, datetime) and end == today.isoformat() and h2 and h2 <= reference.strftime('%H:%M'):
            continue
        standardized = text_value(r.get('local_padronizado'))
        resolved_address = text_value(r.get('endereco'))
        explicit_campus = campus_venue(r)
        here_id = text_value(r.get('here_id'))
        if explicit_campus and location_status != MANUAL and not matches_campus_venue(standardized, resolved_address):
            lat, lng, standardized, resolved_address, here_id = None, None, '', '', ''
        local = ((standardized if location_status in APPROVED else '') or explicit_campus
                 or text_value(r.get('local_informado')) or 'Local não informado')
        address = (resolved_address if location_status in APPROVED else '') or text_value(r.get('endereco_informado'))
        district = (text_value(r.get('bairro')) if location_status in APPROVED else '') or text_value(r.get('bairro_informado'))
        if location_status not in APPROVED:
            here_id = ''
        event = {
            'id': eid, 'nome': text_value(r.get('evento'), 'Evento sem nome'), 'categoria': cat,
            'data_inicio': start, 'data_fim': end, 'hora_inicio': h1 or None, 'hora_fim': h2 or None,
            'local': local, 'endereco': address, 'bairro': district,
            'descricao': text_value(r.get('descricao')), 'lat': lat, 'lng': lng,
            'precisao_localizacao': text_value(r.get('precisao_localizacao')) if lat is not None else '',
            'location_sources': [url for url in r.get('fontes_localizacao') if isinstance(url, str)]
            if isinstance(r.get('fontes_localizacao'), list) and location_status == MANUAL else [],
            'instagram_url': text_value(r.get('url_post')), 'foto': text_value(r.get('foto_url')),
            'identity': publication_identity(r, local, address, district, here_id),
            'reviewed': text_value(r.get('ocorrencia_revisada')),
            'quality': (text_value(r.get('ocorrencia_revisada')) == eid, lat is not None,
                        bool(h1), bool(h2), bool(text_value(r.get('foto_url'))),
                        finite(r.get('confianca_extracao')) or 0),
        }
        candidates.append(event)
    out = []
    for group in group_occurrences(candidates):
        best = max(group, key=lambda event: event['quality'])
        published = {key: value for key, value in best.items()
                     if key not in {'identity', 'quality', 'reviewed', 'location_sources'}}
        located = max(group, key=lambda event: (event['lat'] is not None, event['quality']))
        if located['lat'] is not None and published['lat'] is None:
            published.update({key: located[key] for key in ('lat', 'lng', 'local', 'endereco', 'bairro',
                                                            'precisao_localizacao')})
        published['id'] = group[0]['id']
        published['instagram_urls'] = source_links(group)
        out.append(published)
    return out

def render_webgis(df, template_path: Path):
    reference=datetime.now(ZoneInfo('America/Sao_Paulo'))
    template=template_path.read_text(encoding="utf-8"); events=json.dumps(events_for_webgis(df,reference),ensure_ascii=False,indent=2,allow_nan=False).replace("</","<\\/")
    pattern=re.compile(r"/\* EVENTOS_INICIO.*?\*/\s*let eventos = .*?;\s*/\* EVENTOS_FIM \*/",re.DOTALL)
    replacement=f"/* EVENTOS_INICIO — gerado pela aplicação */\nlet eventos = {events};\n/* EVENTOS_FIM */"
    result,count=pattern.subn(lambda _:replacement,template,count=1)
    if count!=1: raise RuntimeError("Bloco de eventos não encontrado no template WebGIS.")
    updated=reference.strftime('%d/%m/%Y às %H:%M')
    result=result.replace('__DATA_ATUALIZACAO__',reference.date().isoformat())
    result=result.replace('<!-- ULTIMA_ATUALIZACAO -->','Última atualização: '+updated)
    return result


def generate_webgis(df, template_path: Path, output_path: Path):
    from app.utils.atomic_file import write_text

    write_text(output_path, render_webgis(df, template_path))
    return len(events_for_webgis(df))

def export_all(final, posts, extraction_failures, location_failures, output_dir: Path, template_path: Path):
    # Only the distributable WebMap is written here. Pipeline history remains separate.
    output_dir.mkdir(parents=True,exist_ok=True)
    webgis=output_dir/'qual_a_boa_floripa.html'
    generate_webgis(final,template_path,webgis)
    return webgis
