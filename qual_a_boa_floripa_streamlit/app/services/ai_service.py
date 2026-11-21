EVENTOS_SCHEMA = {
    "type": "json_schema",
    "name": "eventos_do_post",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "eventos": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "evento": {
                            "type": "string"
                        },
                        "responsavel_evento": {
                            "type": ["string", "null"]
                        },

                        "categoria": {
                            "type": "string",
                            "enum": [
                                "exposicoes",
                                "apresentacoes",
                                "musica",
                                "feiras",
                                "esportes",
                                "gastronomia",
                                "cinema_audiovisual",
                                "cursos_oficinas",
                                "festivais",
                                "encontros"
                            ]
                        },

                        "data_inicio": {
                            "type": [
                                "string",
                                "null"
                            ]
                        },

                        "data_fim": {
                            "type": [
                                "string",
                                "null"
                            ]
                        },

                        "horario_inicio": {
                            "type": [
                                "string",
                                "null"
                            ]
                        },

                        "horario_fim": {
                            "type": [
                                "string",
                                "null"
                            ]
                        },

                        "local_informado": {
                            "type": [
                                "string",
                                "null"
                            ]
                        },

                        "endereco_informado": {
                            "type": [
                                "string",
                                "null"
                            ]
                        },

                        "bairro_informado": {
                            "type": [
                                "string",
                                "null"
                            ]
                        },

                        "referencia_local": {
                            "type": [
                                "string",
                                "null"
                            ]
                        },

                        "descricao": {
                            "type": "string"
                        },

                        "confianca_extracao": {
                            "type": "number",
                            "minimum": 0,
                            "maximum": 1
                        },

                        "observacoes": {
                            "type": [
                                "string",
                                "null"
                            ]
                        }
                    },

                    "required": [
                        "evento",
                        "responsavel_evento",
                        "categoria",
                        "data_inicio",
                        "data_fim",
                        "horario_inicio",
                        "horario_fim",
                        "local_informado",
                        "endereco_informado",
                        "bairro_informado",
                        "referencia_local",
                        "descricao",
                        "confianca_extracao",
                        "observacoes"
                    ],

                    "additionalProperties": False
                }
            }
        },

        "required": [
            "eventos"
        ],

        "additionalProperties": False
    }
}



import base64, io, json, time
import re
from datetime import date, time as clock_time
from hashlib import sha256
import pandas as pd
import requests
from PIL import Image
from openai import AuthenticationError, OpenAI, PermissionDeniedError, RateLimitError
from app.services.event_identity import normalized, outside_agenda
from app.services.spatial_quality import address_parts, street_identity

LOCATION_SCHEMA_FIELDS = {
    **{name: {"type": ["string", "null"]} for name in (
        "evidencia_local", "evidencia_endereco", "origem_evidencia_local",
        "origem_evidencia_endereco", "municipio_informado", "instituicao_local")},
    "confianca_local": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
}
_event_schema = EVENTOS_SCHEMA["schema"]["properties"]["eventos"]["items"]
_event_schema["properties"].update(LOCATION_SCHEMA_FIELDS)
_event_schema["required"].extend(LOCATION_SCHEMA_FIELDS)

def image_to_data_url(url: str, config) -> str:
    r=requests.get(url,headers={"User-Agent":"Mozilla/5.0","Referer":"https://www.instagram.com/"},timeout=30)
    r.raise_for_status()
    image=Image.open(io.BytesIO(r.content)).convert("RGB")
    image.thumbnail((config.max_image_side,config.max_image_side),Image.Resampling.LANCZOS)
    buff=io.BytesIO(); image.save(buff,format="JPEG",quality=config.jpeg_quality,optimize=True)
    return "data:image/jpeg;base64,"+base64.b64encode(buff.getvalue()).decode("ascii")

EXTRACTION_INSTRUCTIONS = """Analise publicações do Instagram como uma agenda de eventos. Considere legenda e imagens; extraia zero, um ou vários eventos concretos. Resolva datas relativas pela data da publicação, nunca pela data atual. Datas YYYY-MM-DD, horários HH:MM. Não invente informações: use null quando ausentes. Local é somente o espaço; endereço e bairro somente se explícitos. Descrição curta e factual.
PERÍODO: procure a data de encerramento tanto na legenda quanto em todas as imagens, inclusive expressões 'em cartaz até', 'visitação de ... a ...', 'temporada' e 'prorrogada até'. Para exposição contínua, data_inicio é o início da visitação e data_fim é o último dia de visitação, inclusivo; não confunda inauguração com encerramento. Se apenas a abertura é anunciada e não há término, data_fim=null e registre 'Encerramento não informado' em observacoes. Para evento explicitamente de um dia, início e fim são a mesma data. Não use data de inscrição, venda de ingresso ou publicação como término. Resolva viradas de mês/ano pelo contexto. Datas isoladas não consecutivas devem gerar eventos separados, não um intervalo contínuo. Em caso de conflito sem solução, use null e explique em observacoes. Informe restrições de visitação (dias fechados) na descrição quando existirem.
"""
EXTRACTION_INSTRUCTIONS += '''\nCATEGORIA: escolha exatamente uma categoria pelo foco principal do evento, usando estas definições:
 - exposicoes: mostras de arte, fotografia, patrimônio, ciência, acervos, instalações e demais conteúdos expostos ao público.
 - apresentacoes: performances ao vivo, como teatro, dança, circo, stand-up, recitais e intervenções artísticas.
 - musica: shows, concertos, apresentações musicais, DJs, rodas de samba, festivais musicais de pequeno porte e eventos centrados em música.
 - feiras: eventos organizados em estandes, barracas ou expositores, voltados à comercialização, divulgação ou apresentação de produtos, serviços e produções locais.
 - esportes: competições, torneios, corridas, jogos, campeonatos e demais eventos cujo foco principal seja uma prática esportiva.
 - gastronomia: eventos voltados à culinária, degustações, experiências gastronômicas, festivais de comida, bebidas e atividades relacionadas.
 - cinema_audiovisual: sessões de cinema, mostras, exibições, lançamentos, cineclubes e eventos relacionados a produções audiovisuais.
 - cursos_oficinas: atividades de aprendizado ou capacitação com caráter prático, técnico, artístico, profissional ou educativo.
 - festivais: eventos de maior programação ou duração, normalmente compostos por diversas atrações, atividades ou apresentações sob um mesmo tema.
 - encontros: palestras, seminários, debates, rodas de conversa, congressos e outros eventos voltados à troca de conhecimento, experiências ou discussão de temas.
Em eventos híbridos, classifique pela atividade central anunciada, não apenas por uma atração secundária. Use festivais para programação ampla e diversa; festivais musicais pequenos permanecem em musica.'''
EXTRACTION_INSTRUCTIONS += '\nHORÁRIOS: preencha início e fim somente quando explicitamente informados no post ou nas imagens. Se faltar um deles, retorne null nesse campo. Nunca estime duração, copie o início para o fim ou use meia-noite/23:59 como substitutos. Não use horário da publicação, de abertura do estabelecimento ou de outro evento.'
EXTRACTION_INSTRUCTIONS += """
ESCOPO: publique apenas atividades culturais, esportivas ou de lazer concretas. Ignore comunicados administrativos, matrículas, rematrículas, inscrições ou venda de ingressos quando o post anuncia somente o prazo, sem uma atividade concreta com data própria. Uma rematrícula para veteranos do Sesc não é um evento. Cultos, missas e manifestações de finalidade religiosa, como Marcha para Jesus, ficam fora da agenda. Caminhar em uma procissão ou marcha religiosa não é esporte. Em um post misto, extraia somente as atividades elegíveis.
IDENTIDADE: use o título próprio da atividade, sem chamadas promocionais, e mantenha informações que distinguem sessões, edições, artistas ou atrações. Preencha responsavel_evento com artista, grupo ou organização efetivamente responsável quando identificado. O perfil que compartilhou o post não é evidência de responsabilidade. Preserve datas e horários de sessões diferentes.
LOCAL: procure o espaço nos detalhes e na descrição do post e nas imagens. Preserve nomes de unidades, siglas e instituição associada. Por exemplo, registre 'CCE da UFSC' como local, e 'Trindade' como bairro se explicitamente indicado. Um bairro, uma referência próxima ou o endereço da entidade divulgadora não substitui o local.
"""
EXTRACTION_INSTRUCTIONS += """
PAPÉIS GEOGRÁFICOS: o perfil que publicou pode apenas divulgar. O responsável organiza ou realiza. O local de realização é o espaço físico onde o público deve comparecer. O endereço precisa estar associado a esse espaço ou à realização nesta data. Separe endereço de contato, sede do organizador, inscrição, pagamento e retirada de ingressos. Não use nenhum desses endereços como local do evento. Em atividade itinerante, registre o ponto de encontro ou saída quando o material indicar que é onde o público deve se apresentar.
EXEMPLOS: 'UFSC apresenta uma mostra no Teatro Álvaro de Carvalho, Rua Marechal Guilherme, 26' identifica UFSC como responsável, teatro como local e a rua como endereço de realização. 'Secretaria de Cultura apresenta um espetáculo' sem informação adicional deixa local e endereço nulos. 'Palestra no Auditório Garapuvu, Centro de Cultura e Eventos da UFSC' identifica o auditório como local e o centro/UFSC como instituição associada. Use a unidade mais específica explicitamente indicada.
EVIDÊNCIAS: em evidencia_local e evidencia_endereco transcreva apenas um trecho curto e literal que vincule o respectivo valor à realização. Informe a origem de cada trecho como 'legenda' ou 'imagem N', usando o número apresentado antes da imagem. Preencha municipio_informado e instituicao_local somente se explicitamente relacionados ao local do evento. Se faltar evidência, deixe o campo, o trecho e sua origem nulos. Use confianca_local de 0 a 1 como indicador auxiliar de clareza espacial, independente de confianca_extracao. Use null quando não houver localização identificável.
"""
EXTRACTION_VERSION = sha256((EXTRACTION_INSTRUCTIONS + json.dumps(EVENTOS_SCHEMA, sort_keys=True)).encode()).hexdigest()[:16]


def validate_event(event, caption, attached):
    issues = []
    for fields, parser, size in ((('data_inicio', 'data_fim'), date.fromisoformat, 10),
                                  (('horario_inicio', 'horario_fim'), clock_time.fromisoformat, 5)):
        for field in fields:
            value = event.get(field)
            if value is not None:
                try:
                    if len(value) != size or parser(value).isoformat()[:size] != value:
                        raise ValueError()
                except (TypeError, ValueError):
                    event[field] = None
                    issues.append(f'{field} inválido')
    if event.get('data_inicio') and event.get('data_fim') and event['data_fim'] < event['data_inicio']:
        event['data_fim'] = None
        issues.append('Período invertido')
    for field, evidence, origin in (
        ('local_informado', 'evidencia_local', 'origem_evidencia_local'),
        ('endereco_informado', 'evidencia_endereco', 'origem_evidencia_endereco'),
    ):
        if evidence not in event:  # compatibility with previous structured results
            continue
        excerpt = normalized(event.get(evidence))
        source = normalized(event.get(origin))
        verified = excerpt and ((source == 'legenda' and f' {excerpt} ' in f' {normalized(caption)} ')
                                or source in attached)
        value = event.get(field)
        if verified and value:
            if field == 'local_informado':
                verified = f' {normalized(value)} ' in f' {excerpt} '
            else:
                street, number = address_parts(value)
                name = street_identity(street)
                verified = bool(name and f' {name} ' in f' {excerpt} '
                                and (not number or f' {number} ' in f' {excerpt} '))
        if field == 'endereco_informado' and re.search(
            r'\b(?:endereco|local) (?:de |para )?(?:inscricoes|contato|retirada|pagamento)\b|'
            r'\b(?:sede administrativa|inscricoes (?:na|no)|retirada de ingressos)\b', excerpt
        ):
            verified = False
        if not verified:
            if event.get(field):
                issues.append(f'{field} sem evidência verificável')
            event[field] = event[evidence] = event[origin] = None
    if issues:
        event['observacoes'] = ' | '.join(filter(None, [event.get('observacoes'), *issues]))
    return event


def interpret_post(client: OpenAI, post, config):
    published=pd.Timestamp(post["data_publicacao"])
    caption = post.get("legenda")
    caption = caption if isinstance(caption, str) else ""
    images = post.get("imagens")
    images = images if isinstance(images, list) else []
    prompt=f"Data da publicação: {published.strftime('%Y-%m-%d')}\nPerfil: @{post.get('perfil') or ''}\nLEGENDA:\n{caption}"
    content=[{"type":"input_text","text":prompt}]; errors=[]; attached=set()
    for i,url in enumerate(list(dict.fromkeys(images))[:config.max_images_per_post],1):
        try:
            image = image_to_data_url(url,config)
            content.extend([{"type":"input_text","text":f"Imagem {i}:"},
                            {"type":"input_image","image_url":image,"detail":"high"}])
            attached.add(f'imagem {i}')
        except Exception as exc: errors.append(f"imagem {i}: {type(exc).__name__}")
    if errors and len(content) == 1 and not caption.strip():
        raise RuntimeError('Imagens indisponíveis e post sem legenda. Análise pendente.')
    response=client.responses.create(model=config.ai_model,input=[{"role":"system","content":EXTRACTION_INSTRUCTIONS},{"role":"user","content":content}],text={"format":EVENTOS_SCHEMA},max_output_tokens=4000,store=False)
    if getattr(response, 'status', 'completed') != 'completed':
        raise RuntimeError('A resposta do GPT foi interrompida ou incompleta.')
    found = json.loads(response.output_text)['eventos']
    if not isinstance(found, list) or any(not isinstance(event, dict) for event in found):
        raise ValueError('O GPT retornou uma lista de eventos inválida.')
    if not found and errors:
        raise RuntimeError('A análise não encontrou eventos e há imagens indisponíveis. Post pendente para nova tentativa.')
    return [validate_event(event, caption, attached) for event in found
            if not outside_agenda(event.get('evento'))], "; ".join(errors)

def extract_events(api_key: str, posts: pd.DataFrame, config, progress=None, on_result=None):
    client=OpenAI(api_key=api_key, timeout=90.0); events=[]; failures=[]; total=max(len(posts),1)
    for n,(idx,post) in enumerate(posts.iterrows(),1):
        try:
            found,img_errors=interpret_post(client,post,config)
            images=post.get("imagens") or []
            photo_url=images[0] if isinstance(images,list) and images else None
            for event in found:
                event.update({"perfil":post.get("perfil"),"data_publicacao":post.get("data_publicacao"),"url_post":post.get("url_post"),"shortcode":post.get("shortcode"),"foto_url":photo_url,"erros_imagem":img_errors or None})
                events.append(event)
        except Exception as exc:
            error=repr(exc)
            failures.append({"indice_post":idx,"url_post":post.get("url_post"),"erro":error})
            if on_result: on_result(idx,post,None,error)
            if isinstance(exc, (AuthenticationError, PermissionDeniedError)) or (
                isinstance(exc, RateLimitError) and exc.code == 'insufficient_quota'
            ):
                raise RuntimeError('OpenAI recusou a execução. Confira as credenciais e o saldo antes de retomar.') from exc
        else:
            if on_result: on_result(idx,post,found,None)
        if progress: progress(n/total, f"Interpretando posts: {n}/{len(posts)}")
        time.sleep(config.ai_interval_seconds)
    cols=["evento","responsavel_evento","categoria","data_inicio","data_fim","horario_inicio","horario_fim","local_informado","endereco_informado","bairro_informado","referencia_local","descricao","confianca_extracao","observacoes","perfil","data_publicacao","url_post","shortcode","foto_url","erros_imagem"]
    return pd.DataFrame(events,columns=[*cols, *LOCATION_SCHEMA_FIELDS]), pd.DataFrame(failures)
