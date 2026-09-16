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

def interpret_post(client: OpenAI, post, config):
    published=pd.Timestamp(post["data_publicacao"])
    prompt=f"""Analise esta publicação do Instagram como uma agenda de eventos.\nData da publicação: {published.strftime('%Y-%m-%d')}\nPerfil: @{post.get('perfil') or ''}\nURL: {post.get('url_post') or ''}\nLEGENDA:\n{post.get('legenda') or ''}\n\nExtraia somente eventos concretos. Considere legenda e imagens. Um post pode conter zero, um ou vários eventos. Resolva datas relativas usando a data da publicação. Datas em YYYY-MM-DD e horários HH:MM. Use null quando não houver informação e não invente dados. Em local_informado registre somente o espaço; endereço e bairro somente se explicitamente informados. Descrição curta e factual."""
    prompt += '''\nCATEGORIA: escolha exatamente uma categoria pelo foco principal do evento, usando estas definições:
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
    prompt += '\nHORÁRIOS: preencha início e fim somente quando explicitamente informados no post ou nas imagens. Se faltar um deles, retorne null nesse campo. Nunca estime duração, copie o início para o fim ou use meia-noite/23:59 como substitutos. Não use horário da publicação, de abertura do estabelecimento ou de outro evento.'
    content=[{"type":"input_text","text":prompt}]; errors=[]
    for i,url in enumerate((post.get("imagens") or [])[:config.max_images_per_post],1):
        try: content.append({"type":"input_image","image_url":image_to_data_url(url,config),"detail":"high"})
        except Exception as exc: errors.append(f"imagem {i}: {type(exc).__name__}")
    response=client.responses.create(model=config.ai_model,input=[{"role":"user","content":content}],text={"format":EVENTOS_SCHEMA},max_output_tokens=4000,store=False)
    return json.loads(response.output_text).get("eventos",[]), "; ".join(errors)

def extract_events(api_key: str, posts: pd.DataFrame, config, progress=None):
    client=OpenAI(api_key=api_key); events=[]; failures=[]; total=max(len(posts),1)
    for n,(idx,post) in enumerate(posts.iterrows(),1):
        try:
            found,img_errors=interpret_post(client,post,config)
            for event in found:
                event.update({"perfil":post.get("perfil"),"data_publicacao":post.get("data_publicacao"),"url_post":post.get("url_post"),"shortcode":post.get("shortcode"),"erros_imagem":img_errors or None})
                events.append(event)
        except Exception as exc: failures.append({"indice_post":idx,"url_post":post.get("url_post"),"erro":repr(exc)})
        if progress: progress(n/total, f"Interpretando posts: {n}/{len(posts)}")
        time.sleep(config.ai_interval_seconds)
    cols=["evento","categoria","data_inicio","data_fim","horario_inicio","horario_fim","local_informado","endereco_informado","bairro_informado","referencia_local","descricao","confianca_extracao","observacoes","perfil","data_publicacao","url_post","shortcode","erros_imagem"]
    return pd.DataFrame(events,columns=cols), pd.DataFrame(failures)
