import json, math, re, unicodedata
from difflib import SequenceMatcher
import pandas as pd
import requests
from openai import OpenAI

HERE_DISCOVER_URL="https://discover.search.hereapi.com/v1/discover"
HERE_GEOCODE_URL="https://geocode.search.hereapi.com/v1/geocode"

class HereAuthenticationError(RuntimeError):
    pass


def request_here(url, parameters, key, timeout=30):
    try:
        response = requests.get(url, params={**parameters, 'apiKey': key.strip()}, timeout=timeout)
        if response.status_code in (401, 403):
            raise HereAuthenticationError(
                f'HERE: acesso recusado (HTTP {response.status_code}). Confira a API key, '
                'o acesso a Geocoding & Search e as restrições de Trusted Domains no portal HERE.'
            )
        if not response.ok:
            raise RuntimeError(f'HERE: falha HTTP {response.status_code}.')
        return response.json().get('items', [])
    except requests.RequestException:
        raise RuntimeError('Não foi possível conectar à HERE. Confira a conexão e tente novamente.') from None


def validate_here_key(key, config):
    if not key.strip():
        raise HereAuthenticationError('Informe a API key da HERE.')
    request_here(HERE_DISCOVER_URL, {'at': f'{config.reference_lat},{config.reference_lon}', 'q': config.reference_city, 'limit': 1}, key, config.here_timeout)

def configure(api_key, openai_key, config):
    global HERE_API_KEY, openai_client, MODELO_IA, CIDADE_REFERENCIA, ESTADO_REFERENCIA, PAIS_REFERENCIA, CODIGO_PAIS, CENTRO_REFERENCIA, RAIO_MAXIMO_KM, HERE_LIMITE_CANDIDATOS, HERE_IDIOMA, HERE_TIMEOUT, CONFIANCA_MINIMA_LOCALIZACAO, DIFERENCA_MINIMA_CANDIDATOS, USAR_BUSCA_WEB_FALLBACK
    HERE_API_KEY=api_key; openai_client=OpenAI(api_key=openai_key); MODELO_IA=config.ai_model
    CIDADE_REFERENCIA=config.reference_city; ESTADO_REFERENCIA=config.reference_state; PAIS_REFERENCIA=config.reference_country; CODIGO_PAIS=config.country_code
    CENTRO_REFERENCIA=(config.reference_lat,config.reference_lon); RAIO_MAXIMO_KM=config.max_radius_km
    HERE_LIMITE_CANDIDATOS=config.here_candidate_limit; HERE_IDIOMA=config.here_language; HERE_TIMEOUT=config.here_timeout
    CONFIANCA_MINIMA_LOCALIZACAO=config.min_location_confidence; DIFERENCA_MINIMA_CANDIDATOS=config.min_candidate_difference; USAR_BUSCA_WEB_FALLBACK=config.use_web_fallback

FALLBACK_LOCAL_SCHEMA = {
    "type": "json_schema",
    "name": "fallback_local_evento",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "local_sugerido": {
                "type": [
                    "string",
                    "null"
                ]
            },

            "endereco_sugerido": {
                "type": [
                    "string",
                    "null"
                ]
            },

            "bairro_sugerido": {
                "type": [
                    "string",
                    "null"
                ]
            },

            "fonte_web": {
                "type": [
                    "string",
                    "null"
                ]
            },

            "confianca_web": {
                "type": "number",
                "minimum": 0,
                "maximum": 1
            },

            "justificativa": {
                "type": "string"
            }
        },

        "required": [
            "local_sugerido",
            "endereco_sugerido",
            "bairro_sugerido",
            "fonte_web",
            "confianca_web",
            "justificativa"
        ],

        "additionalProperties": False
    }
}


def texto_valido(valor):
    """
    Retorna texto limpo ou string vazia.
    """
    if valor is None:
        return ""

    try:
        if pd.isna(valor):
            return ""
    except (TypeError, ValueError):
        pass

    texto = str(valor).strip()

    if texto.lower() in {
        "",
        "none",
        "null",
        "nan"
    }:
        return ""

    return texto


def normalizar_texto(texto):
    """
    Remove acentos e caracteres especiais para
    comparação entre nomes e endereços.
    """
    texto = texto_valido(texto)

    if not texto:
        return ""

    texto = unicodedata.normalize(
        "NFKD",
        texto
    )

    texto = "".join(
        caractere
        for caractere in texto
        if not unicodedata.combining(
            caractere
        )
    )

    texto = texto.lower()

    texto = re.sub(
        r"[^a-z0-9\s]",
        " ",
        texto
    )

    texto = re.sub(
        r"\s+",
        " ",
        texto
    ).strip()

    return texto


def similaridade_textual(texto_a, texto_b):
    """
    Calcula similaridade combinando sequência
    textual e palavras em comum.
    """
    texto_a = normalizar_texto(
        texto_a
    )

    texto_b = normalizar_texto(
        texto_b
    )

    if not texto_a or not texto_b:
        return 0.0

    score_sequencia = SequenceMatcher(
        None,
        texto_a,
        texto_b
    ).ratio()

    palavras_a = set(
        texto_a.split()
    )

    palavras_b = set(
        texto_b.split()
    )

    uniao = palavras_a | palavras_b

    score_palavras = (
        len(palavras_a & palavras_b)
        / len(uniao)
        if uniao
        else 0.0
    )

    return (
        score_sequencia * 0.60
        + score_palavras * 0.40
    )


def haversine_km(
    lat1,
    lon1,
    lat2,
    lon2
):
    """
    Calcula a distância aproximada entre
    duas coordenadas geográficas.
    """
    raio_terra = 6371.0088

    p1 = math.radians(lat1)

    p2 = math.radians(lat2)

    delta_lat = math.radians(
        lat2 - lat1
    )

    delta_lon = math.radians(
        lon2 - lon1
    )

    a = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(p1)
        * math.cos(p2)
        * math.sin(delta_lon / 2) ** 2
    )

    return (
        2
        * raio_terra
        * math.asin(math.sqrt(a))
    )


def montar_consultas_here(evento):
    """
    Monta consultas progressivas para a HERE.
    As consultas mais específicas são executadas primeiro.
    """
    evento_nome = texto_valido(
        evento.get("evento")
    )

    local = texto_valido(
        evento.get("local_informado")
    )

    endereco = texto_valido(
        evento.get("endereco_informado")
    )

    bairro = texto_valido(
        evento.get("bairro_informado")
    )

    referencia = texto_valido(
        evento.get("referencia_local")
    )

    consultas = []

    if local and endereco:
        consultas.append(
            f"{local}, {endereco}, "
            f"{CIDADE_REFERENCIA}, "
            f"{ESTADO_REFERENCIA}"
        )

    if endereco:
        consultas.append(
            f"{endereco}, "
            f"{CIDADE_REFERENCIA}, "
            f"{ESTADO_REFERENCIA}"
        )

    if local and bairro:
        consultas.append(
            f"{local}, {bairro}, "
            f"{CIDADE_REFERENCIA}, "
            f"{ESTADO_REFERENCIA}"
        )

    if local and referencia:
        consultas.append(
            f"{local}, {referencia}, "
            f"{CIDADE_REFERENCIA}, "
            f"{ESTADO_REFERENCIA}"
        )

    if local:
        consultas.append(
            f"{local}, "
            f"{CIDADE_REFERENCIA}, "
            f"{ESTADO_REFERENCIA}"
        )

    if evento_nome and bairro:
        consultas.append(
            f"{evento_nome}, {bairro}, "
            f"{CIDADE_REFERENCIA}, "
            f"{ESTADO_REFERENCIA}"
        )

    # Remove consultas repetidas preservando a ordem
    return list(
        dict.fromkeys(consultas)
    )


def requisicao_here(
    url,
    parametros
):
    """
    Executa uma requisição na HERE.
    """
    parametros = dict(parametros)

    parametros.update({
        "apiKey": HERE_API_KEY,
        "lang": HERE_IDIOMA,
        "limit": HERE_LIMITE_CANDIDATOS
    })

    return request_here(url, parametros, HERE_API_KEY, HERE_TIMEOUT)


def pesquisar_here_discover(
    consulta
):
    """
    Pesquisa estabelecimentos e pontos de interesse
    próximos da localização de referência.
    """
    latitude_centro, longitude_centro = (
        CENTRO_REFERENCIA
    )

    return requisicao_here(
        HERE_DISCOVER_URL,
        {
            "at": (
                f"{latitude_centro},"
                f"{longitude_centro}"
            ),

            "q": consulta
        }
    )


def pesquisar_here_geocode(
    endereco
):
    """
    Geocodifica um endereço textual.
    """
    return requisicao_here(
        HERE_GEOCODE_URL,
        {
            "q": endereco,

            "in": (
                f"countryCode:"
                f"{CODIGO_PAIS}"
            )
        }
    )


def extrair_categoria_here(item):
    categorias = item.get(
        "categories",
        []
    )

    nomes = []

    for categoria in categorias:
        nome = (
            categoria.get("name")
            or categoria.get("id")
        )

        if nome:
            nomes.append(nome)

    return nomes


def avaliar_candidato_here(
    evento,
    item,
    consulta,
    origem
):
    """
    Padroniza e calcula a confiança de um
    candidato retornado pela HERE.
    """
    posicao = item.get(
        "position",
        {}
    )

    latitude = posicao.get("lat")

    longitude = posicao.get("lng")

    if latitude is None or longitude is None:
        return None

    latitude = float(latitude)

    longitude = float(longitude)

    distancia_centro = haversine_km(
        CENTRO_REFERENCIA[0],
        CENTRO_REFERENCIA[1],
        latitude,
        longitude
    )

    endereco_here = item.get(
        "address",
        {}
    )

    nome_here = texto_valido(
        item.get("title")
    )

    endereco_formatado = texto_valido(
        endereco_here.get("label")
    )

    municipio_here = texto_valido(
        endereco_here.get("city")
    )

    estado_here = (
        texto_valido(
            endereco_here.get("state")
        )
        or texto_valido(
            endereco_here.get("stateCode")
        )
    )

    bairro_here = (
        texto_valido(
            endereco_here.get("district")
        )
        or texto_valido(
            endereco_here.get("subdistrict")
        )
    )

    local_informado = (
        texto_valido(
            evento.get("local_informado")
        )
        or texto_valido(
            evento.get("evento")
        )
    )

    endereco_informado = texto_valido(
        evento.get("endereco_informado")
    )

    bairro_informado = texto_valido(
        evento.get("bairro_informado")
    )

    score_nome = similaridade_textual(
        local_informado,
        nome_here
    )

    score_endereco = similaridade_textual(
        endereco_informado,
        endereco_formatado
    )

    score_bairro = similaridade_textual(
        bairro_informado,
        bairro_here
    )

    score_municipio = similaridade_textual(
        CIDADE_REFERENCIA,
        municipio_here
    )

    score_distancia = max(
        0.0,
        1.0
        - distancia_centro
        / RAIO_MAXIMO_KM
    )

    score_here = (
        item.get("scoring", {})
        .get("queryScore", 0)
        or 0
    )

    if endereco_informado:
        confianca = (
            score_nome * 0.30
            + score_endereco * 0.25
            + score_bairro * 0.10
            + score_municipio * 0.15
            + score_distancia * 0.10
            + float(score_here) * 0.10
        )

    else:
        confianca = (
            score_nome * 0.50
            + score_bairro * 0.10
            + score_municipio * 0.15
            + score_distancia * 0.15
            + float(score_here) * 0.10
        )

    return {
        "here_id": item.get("id"),

        "here_result_type": item.get(
            "resultType"
        ),

        "here_categories": (
            extrair_categoria_here(item)
        ),

        "local_padronizado": nome_here,

        "endereco": endereco_formatado,

        "bairro": bairro_here,

        "municipio": municipio_here,

        "uf": estado_here,

        "pais": texto_valido(
            endereco_here.get("countryName")
        ),

        "latitude": latitude,

        "longitude": longitude,

        "distancia_centro_km": round(
            distancia_centro,
            2
        ),

        "here_distance_m": item.get(
            "distance"
        ),

        "score_nome": round(
            score_nome,
            3
        ),

        "score_endereco": round(
            score_endereco,
            3
        ),

        "score_bairro": round(
            score_bairro,
            3
        ),

        "score_municipio": round(
            score_municipio,
            3
        ),

        "score_here": round(
            float(score_here),
            3
        ),

        "confianca_endereco": round(
            confianca,
            3
        ),

        "consulta_here": consulta,

        "origem_localizacao": origem,

        "fonte_endereco": (
            "HERE Geocoding & Search API"
        )
    }


def buscar_candidatos_here(
    evento
):
    """
    Executa consultas no Discover e, quando existe
    endereço, também no Geocode.
    """
    consultas = montar_consultas_here(
        evento
    )

    candidatos = []

    ids_encontrados = set()

    endereco = texto_valido(
        evento.get("endereco_informado")
    )

    if endereco:
        consulta_endereco = (
            f"{endereco}, "
            f"{CIDADE_REFERENCIA}, "
            f"{ESTADO_REFERENCIA}, "
            f"{PAIS_REFERENCIA}"
        )

        itens_geocode = pesquisar_here_geocode(
            consulta_endereco
        )

        for item in itens_geocode:
            candidato = avaliar_candidato_here(
                evento=evento,
                item=item,
                consulta=consulta_endereco,
                origem="here_geocode"
            )

            if candidato is None:
                continue

            identificador = (
                candidato.get("here_id")
                or (
                    candidato["latitude"],
                    candidato["longitude"]
                )
            )

            if identificador not in ids_encontrados:
                ids_encontrados.add(
                    identificador
                )

                candidatos.append(
                    candidato
                )

    for consulta in consultas:
        itens_discover = pesquisar_here_discover(
            consulta
        )

        for item in itens_discover:
            candidato = avaliar_candidato_here(
                evento=evento,
                item=item,
                consulta=consulta,
                origem="here_discover"
            )

            if candidato is None:
                continue

            identificador = (
                candidato.get("here_id")
                or (
                    candidato["latitude"],
                    candidato["longitude"]
                )
            )

            if identificador not in ids_encontrados:
                ids_encontrados.add(
                    identificador
                )

                candidatos.append(
                    candidato
                )

    candidatos.sort(
        key=lambda candidato: (
            candidato[
                "confianca_endereco"
            ]
        ),
        reverse=True
    )

    return candidatos


def pesquisar_local_na_web(
    evento
):
    """
    Fallback: pesquisa o local na web, mas não
    aceita coordenadas fornecidas pela IA.
    """
    prompt = f"""
    Pesquise na web informações sobre o local deste evento.

    Evento:
    {texto_valido(evento.get("evento"))}

    Local informado:
    {texto_valido(evento.get("local_informado"))}

    Endereço informado:
    {texto_valido(evento.get("endereco_informado"))}

    Bairro informado:
    {texto_valido(evento.get("bairro_informado"))}

    Referência local:
    {texto_valido(evento.get("referencia_local"))}

    Perfil:
    @{texto_valido(evento.get("perfil"))}

    Referência territorial:
    {CIDADE_REFERENCIA},
    {ESTADO_REFERENCIA},
    {PAIS_REFERENCIA}.

    Identifique somente:

    - nome provável do estabelecimento ou espaço;
    - endereço encontrado;
    - bairro;
    - URL da fonte consultada.

    Não produza latitude ou longitude.
    Não invente endereço.
    Se não encontrar evidência confiável, retorne null.
    """

    resposta = openai_client.responses.create(
        model=MODELO_IA,

        tools=[
            {
                "type": "web_search",

                "search_context_size": "low",

                "user_location": {
                    "type": "approximate",

                    "country": "BR",

                    "city": CIDADE_REFERENCIA,

                    "region": ESTADO_REFERENCIA
                }
            }
        ],

        input=prompt,

        text={
            "format": FALLBACK_LOCAL_SCHEMA
        },

        max_output_tokens=1800,

        store=False
    )

    return json.loads(
        resposta.output_text
    )


def selecionar_melhor_candidato(
    candidatos
):
    """
    Seleciona o melhor resultado e identifica
    situações ambíguas.
    """
    if not candidatos:
        return None

    melhor = candidatos[0].copy()

    segundo_score = (
        candidatos[1][
            "confianca_endereco"
        ]
        if len(candidatos) > 1
        else 0.0
    )

    diferenca = (
        melhor["confianca_endereco"]
        - segundo_score
    )

    melhor[
        "quantidade_candidatos_here"
    ] = len(candidatos)

    melhor[
        "diferenca_segundo_candidato"
    ] = round(
        diferenca,
        3
    )

    melhor[
        "candidatos_here"
    ] = candidatos

    if (
        melhor["distancia_centro_km"]
        > RAIO_MAXIMO_KM
    ):
        melhor[
            "necessita_revisao"
        ] = True

        melhor[
            "motivo_revisao"
        ] = (
            "Resultado fora do raio "
            "territorial configurado"
        )

    elif (
        melhor["confianca_endereco"]
        < CONFIANCA_MINIMA_LOCALIZACAO
    ):
        melhor[
            "necessita_revisao"
        ] = True

        melhor[
            "motivo_revisao"
        ] = (
            "Baixa compatibilidade entre "
            "o evento e o local encontrado"
        )

    elif (
        len(candidatos) > 1
        and diferenca
        < DIFERENCA_MINIMA_CANDIDATOS
    ):
        melhor[
            "necessita_revisao"
        ] = True

        melhor[
            "motivo_revisao"
        ] = (
            "Existem candidatos concorrentes "
            "com pontuações semelhantes"
        )

    else:
        melhor[
            "necessita_revisao"
        ] = False

        melhor[
            "motivo_revisao"
        ] = None

    return melhor


def localizar_evento(
    evento
):
    """
    Localiza o evento com a HERE e utiliza
    pesquisa web apenas como fallback.
    """
    candidatos = buscar_candidatos_here(
        evento
    )

    melhor = selecionar_melhor_candidato(
        candidatos
    )

    # Aceita diretamente um resultado confiável
    if (
        melhor
        and not melhor.get(
            "necessita_revisao",
            True
        )
    ):
        return melhor

    # Se a HERE retornou um resultado de baixa confiança,
    # guardamos esse resultado antes do fallback.
    melhor_inicial = (
        melhor.copy()
        if melhor
        else None
    )

    if not USAR_BUSCA_WEB_FALLBACK:
        if melhor_inicial:
            return melhor_inicial

        return {
            "latitude": None,
            "longitude": None,
            "necessita_revisao": True,
            "motivo_revisao": (
                "Nenhum candidato encontrado na HERE"
            ),
            "quantidade_candidatos_here": 0,
            "candidatos_here": []
        }

    pesquisa_web = pesquisar_local_na_web(
        evento
    )

    local_web = texto_valido(
        pesquisa_web.get("local_sugerido")
    )

    endereco_web = texto_valido(
        pesquisa_web.get("endereco_sugerido")
    )

    if not local_web and not endereco_web:
        if melhor_inicial:
            melhor_inicial[
                "fonte_web"
            ] = pesquisa_web.get(
                "fonte_web"
            )

            melhor_inicial[
                "justificativa_web"
            ] = pesquisa_web.get(
                "justificativa"
            )

            return melhor_inicial

        return {
            "latitude": None,
            "longitude": None,
            "necessita_revisao": True,
            "motivo_revisao": (
                "Local não encontrado na HERE "
                "nem na pesquisa web"
            ),
            "fonte_web": pesquisa_web.get(
                "fonte_web"
            ),
            "quantidade_candidatos_here": 0,
            "candidatos_here": []
        }

    evento_fallback = evento.copy()

    if local_web:
        evento_fallback[
            "local_informado"
        ] = local_web

    if endereco_web:
        evento_fallback[
            "endereco_informado"
        ] = endereco_web

    bairro_web = texto_valido(
        pesquisa_web.get("bairro_sugerido")
    )

    if bairro_web:
        evento_fallback[
            "bairro_informado"
        ] = bairro_web

    candidatos_fallback = buscar_candidatos_here(
        evento_fallback
    )

    melhor_fallback = selecionar_melhor_candidato(
        candidatos_fallback
    )

    if melhor_fallback:
        melhor_fallback[
            "origem_localizacao"
        ] = "web_fallback_here"

        melhor_fallback[
            "fonte_web"
        ] = pesquisa_web.get(
            "fonte_web"
        )

        melhor_fallback[
            "confianca_web"
        ] = pesquisa_web.get(
            "confianca_web"
        )

        melhor_fallback[
            "justificativa_web"
        ] = pesquisa_web.get(
            "justificativa"
        )

        return melhor_fallback

    if melhor_inicial:
        melhor_inicial[
            "fonte_web"
        ] = pesquisa_web.get(
            "fonte_web"
        )

def geocode_events(events: pd.DataFrame, here_key: str, openai_key: str, config, progress=None, on_result=None):
    configure(here_key,openai_key,config); located=[]; failures=[]; total=max(len(events),1)
    for n,(idx,event) in enumerate(events.iterrows(),1):
        try:
            result=localizar_evento(event)
            if result:
                result["_source_index"]=idx; located.append(result)
                if on_result: on_result(idx,result,None)
            else:
                error="Localização sem resultado; nova tentativa pendente."
                failures.append({"indice_evento":idx,"url_post":event.get("url_post"),"erro":error})
                if on_result: on_result(idx,None,error)
        except HereAuthenticationError:
            raise
        except Exception as exc:
            failures.append({"indice_evento":idx,"url_post":event.get("url_post"),"erro":repr(exc)})
            if on_result: on_result(idx,None,repr(exc))
        if progress: progress(n/total, f"Geocodificando eventos: {n}/{len(events)}")
    geo=pd.DataFrame(located)
    base=events.assign(id=range(1,len(events)+1)).copy()
    if not geo.empty:
        geo=geo.set_index("_source_index")
        base=base.join(geo,how="left",rsuffix="_localizado")
    return base, pd.DataFrame(failures)
