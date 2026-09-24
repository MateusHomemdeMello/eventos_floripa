import json, math, re
from pathlib import Path
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo

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

def events_for_webgis(df, today=None):
    reference = today or datetime.now(ZoneInfo('America/Sao_Paulo'))
    if isinstance(reference, datetime):
        reference = reference.astimezone(ZoneInfo('America/Sao_Paulo')) if reference.tzinfo else reference.replace(tzinfo=ZoneInfo('America/Sao_Paulo'))
    today = reference.date() if isinstance(reference, datetime) else reference
    out=[]
    if df.empty: return out
    for _,r in df.iterrows():
        publish=r.get("publicar_webgis",True)
        if isinstance(publish,str):
            if publish.strip().lower() in {"false","0","não","nao"}: continue
        elif not pd.isna(publish) and not bool(publish): continue
        lat,lng=finite(r.get("latitude")),finite(r.get("longitude")); start=date_iso(r.get("data_inicio"))
        if not start: continue
        if lat is None or lng is None or not(-90<=lat<=90) or not(-180<=lng<=180):
            lat,lng=None,None
        end=date_iso(r.get('data_fim')) or start
        if end < start or end < today.isoformat(): continue
        cat=normalized_category(r.get("categoria"),r.get("evento"),r.get("descricao"))
        try: eid=int(r.get("id"))
        except Exception: eid=len(out)+1
        # History identity is stable even if rows are reordered between exports.
        if text_value(r.get('_post_id')):
            eid=f"{text_value(r.get('_post_id'))}:{text_value(r.get('_event_index'),'0')}"
        h1=hhmm(r.get("horario_inicio")); h2=hhmm(r.get("horario_fim"))
        if isinstance(reference, datetime) and end == today.isoformat() and h2 and h2 <= reference.strftime('%H:%M'):
            continue
        out.append({"id":eid,"nome":text_value(r.get("evento"),"Evento sem nome"),"categoria":cat,"data_inicio":start,"data_fim":date_iso(r.get("data_fim")) or start,"hora_inicio":h1 or None,"hora_fim":h2 or None,"local":text_value(r.get("local_padronizado")) or text_value(r.get("local_informado")) or "Local não informado","endereco":text_value(r.get("endereco")) or text_value(r.get("endereco_informado")),"bairro":text_value(r.get("bairro")) or text_value(r.get("bairro_informado")),"descricao":text_value(r.get("descricao")),"lat":lat,"lng":lng,"instagram_url":text_value(r.get("url_post")),"foto":text_value(r.get("foto_url"))})
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
    output_path.write_text(render_webgis(df,template_path),encoding='utf-8')
    return len(events_for_webgis(df))

def export_all(final, posts, extraction_failures, location_failures, output_dir: Path, template_path: Path):
    # Only the distributable WebMap is written here. Pipeline history remains separate.
    output_dir.mkdir(parents=True,exist_ok=True)
    webgis=output_dir/'qual_a_boa_floripa.html'
    generate_webgis(final,template_path,webgis)
    return webgis
