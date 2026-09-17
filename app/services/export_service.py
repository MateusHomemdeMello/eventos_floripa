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
    today = today or datetime.now(ZoneInfo('America/Sao_Paulo')).date()
    out=[]
    if df.empty or not {"latitude","longitude"}.issubset(df.columns): return out
    for _,r in df.iterrows():
        publish=r.get("publicar_webgis",True)
        if isinstance(publish,str):
            if publish.strip().lower() in {"false","0","não","nao"}: continue
        elif not pd.isna(publish) and not bool(publish): continue
        lat,lng=finite(r.get("latitude")),finite(r.get("longitude")); start=date_iso(r.get("data_inicio"))
        if lat is None or lng is None or not start or not(-90<=lat<=90) or not(-180<=lng<=180): continue
        end=date_iso(r.get('data_fim')) or start
        if end < start or end < today.isoformat(): continue
        cat=normalized_category(r.get("categoria"),r.get("evento"),r.get("descricao"))
        try: eid=int(r.get("id"))
        except Exception: eid=len(out)+1
        h1=hhmm(r.get("horario_inicio")); h2=hhmm(r.get("horario_fim"))
        out.append({"id":eid,"nome":text_value(r.get("evento"),"Evento sem nome"),"categoria":cat,"data_inicio":start,"data_fim":date_iso(r.get("data_fim")) or start,"hora_inicio":h1 or None,"hora_fim":h2 or None,"local":text_value(r.get("local_padronizado")) or text_value(r.get("local_informado")) or "Local não informado","endereco":text_value(r.get("endereco")) or text_value(r.get("endereco_informado")),"bairro":text_value(r.get("bairro")) or text_value(r.get("bairro_informado")),"descricao":text_value(r.get("descricao")),"lat":lat,"lng":lng,"instagram_url":text_value(r.get("url_post")),"foto":""})
    return out

def render_webgis(df, template_path: Path):
    template=template_path.read_text(encoding="utf-8"); events=json.dumps(events_for_webgis(df),ensure_ascii=False,indent=2,allow_nan=False).replace("</","<\\/")
    pattern=re.compile(r"/\* EVENTOS_INICIO.*?\*/\s*let eventos = .*?;\s*/\* EVENTOS_FIM \*/",re.DOTALL)
    replacement=f"/* EVENTOS_INICIO — gerado pela aplicação */\nlet eventos = {events};\n/* EVENTOS_FIM */"
    result,count=pattern.subn(lambda _:replacement,template,count=1)
    if count!=1: raise RuntimeError("Bloco de eventos não encontrado no template WebGIS.")
    updated=datetime.now(ZoneInfo('America/Sao_Paulo')).strftime('%d/%m/%Y às %H:%M')
    result=result.replace('<!-- ULTIMA_ATUALIZACAO -->','Última atualização: '+updated)
    return result


def generate_webgis(df, template_path: Path, output_path: Path):
    output_path.write_text(render_webgis(df,template_path),encoding='utf-8')
    return len(events_for_webgis(df))

def generate_interface_data(df, interface_dir: Path):
    interface_dir.mkdir(parents=True,exist_ok=True)
    updated=datetime.now(ZoneInfo('America/Sao_Paulo')).isoformat(timespec='minutes')
    payload={"atualizado_em":updated,"eventos":events_for_webgis(df)}
    data=json.dumps(payload,ensure_ascii=False,indent=2,allow_nan=False).replace("</","<\\/")
    (interface_dir/'dados.js').write_text('window.QUAL_A_BOA_DATA = '+data+';\n',encoding='utf-8')
    return len(payload['eventos'])

def excel_safe(df):
    out=df.copy()
    for col in out.columns:
        if isinstance(out[col].dtype,pd.DatetimeTZDtype): out[col]=out[col].dt.tz_convert("America/Sao_Paulo").dt.tz_localize(None)
    return out

def export_all(final, posts, extraction_failures, location_failures, output_dir: Path, template_path: Path):
    output_dir.mkdir(parents=True,exist_ok=True); csv=output_dir/'eventos_processados.csv'; xlsx=output_dir/'eventos_processados.xlsx'; webgis=output_dir/'qual_a_boa_floripa.html'
    f,p,ef,lf=map(excel_safe,[final,posts,extraction_failures,location_failures])
    f=f.drop(columns=[name for name in f.columns if str(name).startswith('_')])
    f.to_csv(csv,index=False,encoding='utf-8-sig')
    with pd.ExcelWriter(xlsx,engine='openpyxl') as writer:
        f.to_excel(writer,sheet_name='eventos',index=False); p.to_excel(writer,sheet_name='posts_coletados',index=False)
        if not ef.empty: ef.to_excel(writer,sheet_name='falhas_extracao',index=False)
        if not lf.empty: lf.to_excel(writer,sheet_name='falhas_localizacao',index=False)
    generate_webgis(final,template_path,webgis)
    generate_interface_data(final,output_dir.parent.parent/'interface')
    return csv,xlsx,webgis
