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
import pandas as pd
import requests
from PIL import Image
from openai import OpenAI

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


def interpret_post(client: OpenAI, post, config):
    published=pd.Timestamp(post["data_publicacao"])
    prompt=f"Data da publicação: {published.strftime('%Y-%m-%d')}\nPerfil: @{post.get('perfil') or ''}\nLEGENDA:\n{post.get('legenda') or ''}"
    content=[{"type":"input_text","text":prompt}]; errors=[]
    for i,url in enumerate(list(dict.fromkeys(post.get("imagens") or []))[:config.max_images_per_post],1):
        try: content.append({"type":"input_image","image_url":image_to_data_url(url,config),"detail":"high"})
        except Exception as exc: errors.append(f"imagem {i}: {type(exc).__name__}")
    response=client.responses.create(model=config.ai_model,input=[{"role":"system","content":EXTRACTION_INSTRUCTIONS},{"role":"user","content":content}],text={"format":EVENTOS_SCHEMA},max_output_tokens=4000,store=False)
    return json.loads(response.output_text).get("eventos",[]), "; ".join(errors)

def extract_events(api_key: str, posts: pd.DataFrame, config, progress=None, on_result=None):
    client=OpenAI(api_key=api_key); events=[]; failures=[]; total=max(len(posts),1)
    for n,(idx,post) in enumerate(posts.iterrows(),1):
        try:
            found,img_errors=interpret_post(client,post,config)
            images=post.get("imagens") or []
            photo_url=images[0] if isinstance(images,list) and images else None
            for event in found:
                event.update({"perfil":post.get("perfil"),"data_publicacao":post.get("data_publicacao"),"url_post":post.get("url_post"),"shortcode":post.get("shortcode"),"foto_url":photo_url,"erros_imagem":img_errors or None})
                events.append(event)
            if on_result: on_result(idx,post,found,None)
        except Exception as exc:
            error=repr(exc)
            failures.append({"indice_post":idx,"url_post":post.get("url_post"),"erro":error})
            if on_result: on_result(idx,post,None,error)
        if progress: progress(n/total, f"Interpretando posts: {n}/{len(posts)}")
        time.sleep(config.ai_interval_seconds)
    cols=["evento","categoria","data_inicio","data_fim","horario_inicio","horario_fim","local_informado","endereco_informado","bairro_informado","referencia_local","descricao","confianca_extracao","observacoes","perfil","data_publicacao","url_post","shortcode","foto_url","erros_imagem"]
    return pd.DataFrame(events,columns=cols), pd.DataFrame(failures)
