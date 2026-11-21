import json, math, re, unicodedata, time
from difflib import SequenceMatcher
import pandas as pd
import requests
from openai import OpenAI
from app.services.event_identity import campus_venue, matches_campus_venue, normalized
from app.services.spatial_quality import (
    AUTO, REVIEW, UNKNOWN, address_parts, coordinate, organizer_as_venue, street_identity,
)

GENERIC_VENUE_WORDS = {'de', 'da', 'do', 'das', 'dos', 'e', 'centro', 'teatro', 'espaco',
                       'cultural', 'shopping', 'praca', 'sala', 'auditorio', 'unidade'}


def compatible_venue(expected, candidate):
    expected, candidate = normalized(expected), normalized(candidate)
    expected = expected.replace('universidade federal de santa catarina', 'ufsc')
    candidate = candidate.replace('universidade federal de santa catarina', 'ufsc')
    if expected == candidate:
        return True
    known = {word for word in expected.split() if len(word) >= 3 and not word.isdigit()} - GENERIC_VENUE_WORDS
    found = set(candidate.split())
    return bool(known and len(known & found) >= min(len(known), 2))

HERE_DISCOVER_URL="https://discover.search.hereapi.com/v1/discover"
HERE_GEOCODE_URL="https://geocode.search.hereapi.com/v1/geocode"

class HereAuthenticationError(RuntimeError):
    pass


class HereUnavailableError(RuntimeError):
    pass


def request_here(url, parameters, key, timeout=30):
    for attempt in range(3):
        try:
            return _request_here_once(url, parameters, key, timeout)
        except (HereTemporaryError, requests.RequestException):
            if attempt == 2:
                raise HereUnavailableError('HERE indisponível após 3 tentativas (rede, limite de consultas ou servidor). Tente novamente a etapa de localização.') from None
            time.sleep(2 ** attempt)


class HereTemporaryError(RuntimeError):
    pass


def _request_here_once(url, parameters, key, timeout):
    try:
        response = requests.get(url, params={**parameters, 'apiKey': key.strip()}, timeout=timeout)
        if response.status_code in (401, 403):
            raise HereAuthenticationError(
                f'HERE: acesso recusado (HTTP {response.status_code}). Confira a API key, '
                'o acesso a Geocoding & Search e as restrições de Trusted Domains no portal HERE.'
            )
        if response.status_code == 429 or response.status_code >= 500:
            raise HereTemporaryError()
        if not response.ok:
            raise RuntimeError(f'HERE: falha HTTP {response.status_code}.')
        try:
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get('items'), list):
                raise ValueError()
            return payload['items']
        except ValueError:
            raise HereTemporaryError() from None
    except requests.RequestException:
        raise HereTemporaryError() from None


def validate_here_key(key, config):
    if not key.strip():
        raise HereAuthenticationError('Informe a API key da HERE.')
    request_here(HERE_DISCOVER_URL, {'at': f'{config.reference_lat},{config.reference_lon}', 'q': config.reference_city, 'limit': 1}, key, config.here_timeout)

def configure(api_key, openai_key, config):
    global MUNICIPIOS_ATENDIDOS
    MUNICIPIOS_ATENDIDOS = {normalized(config.reference_city), *(normalized(city) for city in config.allowed_municipalities)}
    global HERE_API_KEY, openai_client, MODELO_IA, CIDADE_REFERENCIA, ESTADO_REFERENCIA, PAIS_REFERENCIA, CODIGO_PAIS, CENTRO_REFERENCIA, RAIO_MAXIMO_KM, HERE_LIMITE_CANDIDATOS, HERE_IDIOMA, HERE_TIMEOUT, CONFIANCA_MINIMA_LOCALIZACAO, DIFERENCA_MINIMA_CANDIDATOS, USAR_BUSCA_WEB_FALLBACK
    HERE_API_KEY=api_key; openai_client=OpenAI(api_key=openai_key, timeout=90.0) if config.use_web_fallback else None; MODELO_IA=config.ai_model
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


FALLBACK_LOCAL_SCHEMA['schema']['properties']['evidencia_web'] = {'type': ['string', 'null']}
FALLBACK_LOCAL_SCHEMA['schema']['required'].append('evidencia_web')


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
    local = texto_valido(evento.get('local_informado'))
    endereco = texto_valido(evento.get('endereco_informado'))
    bairro = texto_valido(evento.get('bairro_informado'))
    referencia = texto_valido(evento.get('referencia_local'))
    municipio = texto_valido(evento.get('municipio_informado')) or CIDADE_REFERENCIA
    consultas = []
    if local and endereco:
        consultas.append(f'{local}, {endereco}, {municipio}, {ESTADO_REFERENCIA}')
    if local and (bairro or referencia):
        consultas.append(f'{local}, {bairro or referencia}, {municipio}, {ESTADO_REFERENCIA}')
    if local:
        consultas.append(f'{local}, {municipio}, {ESTADO_REFERENCIA}')

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
    address = item.get('address') or {}
    name = texto_valido(item.get('title'))
    label = texto_valido(address.get('label'))
    position = item.get('position') or {}
    latitude = coordinate(position.get('lat'), 90)
    longitude = coordinate(position.get('lng'), 180)
    kind = item.get('resultType')
    number_type = item.get('houseNumberType')
    city = texto_valido(address.get('city'))
    district = texto_valido(address.get('district')) or texto_valido(address.get('subdistrict'))
    state = texto_valido(address.get('state')) or texto_valido(address.get('stateCode'))
    explicit_name = texto_valido(evento.get('local_informado'))
    explicit_address = texto_valido(evento.get('endereco_informado'))
    explicit_city = normalized(evento.get('municipio_informado'))
    explicit_district = normalized(evento.get('bairro_informado'))
    organizer = texto_valido(evento.get('responsavel_evento'))

    def reject(reason):
        audit = evento.get('_rejected_here')
        if isinstance(audit, list) and len(audit) < 40:
            record = {'here_id': item.get('id'), 'local_padronizado': name, 'endereco': label,
                      'latitude': latitude, 'longitude': longitude, 'motivo_rejeicao': reason}
            if record not in audit:
                audit.append(record)
        return None

    if not (explicit_name or explicit_address):
        return reject('Sem evidência do local de realização')
    if latitude is None or longitude is None:
        return reject('Coordenadas ausentes ou inválidas')
    if kind not in {'place', 'houseNumber'}:
        return reject('Resultado sem precisão de estabelecimento ou número')
    distance = haversine_km(*CENTRO_REFERENCIA, latitude, longitude)
    if distance > RAIO_MAXIMO_KM:
        return reject('Fora do raio territorial configurado')
    if ((city and normalized(city) not in MUNICIPIOS_ATENDIDOS)
            or (explicit_city and city and explicit_city != normalized(city))
            or (state and normalized(state) not in {normalized(ESTADO_REFERENCIA), 'sc'}
                and normalized(ESTADO_REFERENCIA) == 'santa catarina')
            or (address.get('countryCode') and address['countryCode'] != CODIGO_PAIS)):
        return reject('Município, UF ou país incompatível')
    if explicit_district and district and explicit_district != normalized(district):
        return reject('Bairro incompatível com a fonte')
    if (kind == 'place' and organizer and compatible_venue(organizer, name)
            and (not explicit_name or not compatible_venue(explicit_name, name))):
        return reject('Candidato corresponde à sede do organizador')
    if campus_venue(evento) and kind == 'place' and not matches_campus_venue(name, ''):
        return reject('Candidato não identifica a unidade CCE da UFSC')

    street, number = address_parts(explicit_address)
    candidate_street, candidate_number = address_parts(label)
    candidate_street = street_identity(address.get('street') or candidate_street)
    candidate_number = normalized(address.get('houseNumber')) or candidate_number
    street = street_identity(street)
    if street and candidate_street and street != candidate_street:
        return reject('Logradouro incompatível com o endereço de realização')
    if number and candidate_number and number != candidate_number:
        return reject('Número incompatível com o endereço de realização')

    exact_address = bool(street and number and street == candidate_street and number == candidate_number)
    if kind == 'place' and not (explicit_name and compatible_venue(explicit_name, name)):
        if not (campus_venue(evento) and matches_campus_venue(name, '')):
            return reject('POI diferente do espaço anunciado')
    if kind == 'houseNumber' and not exact_address:
        return reject('Logradouro e número do endereço não confirmados')

    reasons = []
    if not city:
        reasons.append('Município não confirmado')
    if street and not candidate_street or number and not candidate_number:
        reasons.append('Endereço explícito não confirmado integralmente')
    if kind == 'houseNumber' and number_type not in {'PA', 'MPA'}:
        reasons.append('Endereço interpolado ou precisão do ponto desconhecida')
    score_nome = 1.0 if campus_venue(evento) and kind == 'place' else similaridade_textual(explicit_name, name)
    score_endereco = 1.0 if exact_address else similaridade_textual(explicit_address, label)
    score_bairro = similaridade_textual(explicit_district, district)
    score_municipio = float(bool(city))
    score_here = float((item.get('scoring') or {}).get('queryScore') or 0)
    if kind == 'houseNumber':
        confidence = 0.90 + 0.10 * score_here
    elif explicit_address:
        confidence = 0.52 * score_nome + 0.30 * score_endereco + 0.08 * score_municipio + 0.10 * score_here
    else:
        confidence = 0.80 * score_nome + 0.10 * score_municipio + 0.10 * score_here

    return {
        "here_id": item.get("id"),

        "here_result_type": item.get(
            "resultType"
        ),

        "here_categories": (
            extrair_categoria_here(item)
        ),

        "local_padronizado": name if kind == 'place' else None,
        "precisao_localizacao": "poi" if kind == 'place' else "endereco",
        "tipo_numero_here": number_type,
        "acessos_here": item.get('access') or [],
        "impedimentos_localizacao": reasons,

        "endereco": label,

        "bairro": district,

        "municipio": city,

        "uf": state,

        "pais": texto_valido(
            address.get("countryName")
        ),

        "latitude": latitude,

        "longitude": longitude,

        "distancia_centro_km": round(
            distance,
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
            score_here,
            3
        ),

        "confianca_endereco": round(
            confidence,
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
    venue = normalized(evento.get('local_informado'))
    district = normalized(evento.get('bairro_informado'))
    if (not venue or venue == district) and not texto_valido(evento.get('endereco_informado')):
        return []
    if organizer_as_venue(evento):
        evento = {**evento, 'local_informado': None}
    venue = normalized(evento.get('local_informado'))
    district = normalized(evento.get('bairro_informado'))
    address = texto_valido(evento.get('endereco_informado'))
    if (not venue or venue == district) and not address:
        return []
    city = texto_valido(evento.get('municipio_informado')) or CIDADE_REFERENCIA
    candidates = {}

    def add(items, query, origin):
        for item in items:
            candidate = avaliar_candidato_here(evento, item, query, origin)
            if candidate is None:
                continue
            identity = candidate['here_id'] or (
                candidate['here_result_type'], normalized(item.get('title')),
                normalized(candidate['endereco']), candidate['latitude'], candidate['longitude'],
            )
            previous = candidates.get(identity)
            if previous is None or candidate['confianca_endereco'] > previous['confianca_endereco']:
                candidates[identity] = candidate

    if address:
        query = f'{address}, {city}, {ESTADO_REFERENCIA}, {PAIS_REFERENCIA}'
        add(pesquisar_here_geocode(query), query, 'here_geocode')
    for query in montar_consultas_here(evento):
        add(pesquisar_here_discover(query), query, 'here_discover')
    candidatos = sorted(candidates.values(), key=lambda item: (
        not item['impedimentos_localizacao'], item['precisao_localizacao'] == 'poi',
        item['confianca_endereco'],
    ), reverse=True)

    return candidatos


def pesquisar_local_na_web(
    evento
):
    """
    Fallback: pesquisa o local na web, mas não
    aceita coordenadas fornecidas pela IA.
    """
    prompt = f"""
    Pesquise o espaço físico onde esta ocorrência realmente acontece.
    Confirme a relação entre endereço, evento, data e espaço. A sede do organizador,
    o perfil divulgador e o endereço para inscrição ou retirada de ingressos não são
    evidência de realização. Se só houver uma página institucional, retorne null.

    Data da ocorrência:
    {texto_valido(evento.get("data_inicio"))}

    Responsável:
    {texto_valido(evento.get("responsavel_evento"))}

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
    - URL da fonte consultada;
    - trecho curto literal da fonte que sustenta a realização no espaço;
    - justificativa da relação entre endereço e ocorrência.

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

        include=["web_search_call.action.sources"],
        max_output_tokens=1800,

        store=False
    )

    suggestion = json.loads(resposta.output_text)
    verified = set()
    for item in getattr(resposta, 'output', []):
        action = getattr(item, 'action', None)
        for source in getattr(action, 'sources', None) or []:
            url = getattr(source, 'url', None)
            if url:
                verified.add(url)
        for content in getattr(item, 'content', None) or []:
            for citation in getattr(content, 'annotations', None) or []:
                if getattr(citation, 'type', '') == 'url_citation':
                    verified.add(citation.url)
    if suggestion.get('fonte_web') not in verified:
        return {}
    return suggestion


def selecionar_melhor_candidato(candidatos):
    if not candidatos:
        return None
    best = candidatos[0].copy()
    peers = [candidate for candidate in candidatos[1:] if
             candidate.get('precisao_localizacao') == best.get('precisao_localizacao')
             and not candidate.get('impedimentos_localizacao')]
    difference = best['confianca_endereco'] - peers[0]['confianca_endereco'] if peers else None
    reasons = list(best.get('impedimentos_localizacao', []))
    if best.get('precisao_localizacao') == 'poi' and any(
        normalized(peer.get('local_padronizado')) == normalized(best.get('local_padronizado'))
        and ((peer.get('municipio') and best.get('municipio') and
              normalized(peer['municipio']) != normalized(best['municipio']))
             or (peer.get('bairro') and best.get('bairro') and
                 normalized(peer['bairro']) != normalized(best['bairro'])))
        for peer in peers
    ):
        reasons.append('Estabelecimentos homônimos em territórios distintos')
    if best['confianca_endereco'] < CONFIANCA_MINIMA_LOCALIZACAO:
        reasons.append('Compatibilidade abaixo do limiar configurado')
    if difference is not None and difference < DIFERENCA_MINIMA_CANDIDATOS:
        reasons.append('Candidatos concorrentes sem margem suficiente')
    best.update(quantidade_candidatos_here=len(candidatos),
                diferenca_segundo_candidato=round(difference, 3) if difference is not None else None,
                candidatos_here=candidatos, necessita_revisao=bool(reasons),
                motivo_revisao='; '.join(reasons) or None,
                status_localizacao=REVIEW if reasons else AUTO)
    return best


def localizar_evento(evento):
    query = dict(evento)
    campus = campus_venue(query)
    if campus:
        query['local_informado'] = campus
    if organizer_as_venue(query):
        query['local_informado'] = None
        if not query.get('evidencia_endereco'):
            query['endereco_informado'] = None
    name = normalized(query.get('local_informado'))
    address = texto_valido(query.get('endereco_informado'))
    if not address and (not name or name == normalized(query.get('bairro_informado'))):
        return {'status_localizacao': UNKNOWN, 'necessita_revisao': True,
                'motivo_revisao': 'Sem evidência do local de realização',
                'latitude': None, 'longitude': None}
    rejected = []
    query['_rejected_here'] = rejected
    best = selecionar_melhor_candidato(buscar_candidatos_here(query))
    if best and not best.get('necessita_revisao', True):
        return {**best, 'candidatos_rejeitados_here': rejected}

    if USAR_BUSCA_WEB_FALLBACK:
        try:
            suggestion = pesquisar_local_na_web(query)
        except (RuntimeError, ValueError) as exc:
            if not best:
                raise
            best['aviso_localizacao'] = str(exc)
        except Exception:
            if not best:
                raise RuntimeError('Busca web indisponível; localização pendente.') from None
            best['aviso_localizacao'] = 'Busca web indisponível; candidato HERE preservado para revisão.'
        else:
            if suggestion.get('fonte_web') and suggestion.get('evidencia_web'):
                fields = ('local_informado', 'endereco_informado', 'bairro_informado')
                auxiliary = ('local_sugerido', 'endereco_sugerido', 'bairro_sugerido')
                enriched = {**query, **{field: texto_valido(suggestion.get(source))
                                       for field, source in zip(fields, auxiliary)
                                       if not texto_valido(query.get(field)) and texto_valido(suggestion.get(source))}}
                if enriched != query:
                    fallback = selecionar_melhor_candidato(buscar_candidatos_here(enriched))
                    if fallback:
                        best = {**fallback, 'status_localizacao': REVIEW, 'necessita_revisao': True,
                                'origem_localizacao': 'web_fallback_here',
                                'motivo_revisao': 'Confirme a evidência auxiliar da web',
                                'sugestao_web': suggestion, 'fonte_web': suggestion.get('fonte_web')}

    if best:
        return {**best, 'candidatos_rejeitados_here': rejected}
    return {'status_localizacao': REVIEW, 'latitude': None, 'longitude': None,
            'necessita_revisao': True, 'motivo_revisao': 'Nenhum candidato HERE compatível',
            'candidatos_here': [], 'candidatos_rejeitados_here': rejected}


def geocode_events(events: pd.DataFrame, here_key: str, openai_key: str, config, progress=None, on_result=None,
                   resolve_cached=None):
    configure(here_key,openai_key,config); located=[]; failures=[]; total=max(len(events),1)
    for n,(idx,event) in enumerate(events.iterrows(),1):
        try:
            result = (resolve_cached(event) if resolve_cached else None) or localizar_evento(event)
        except (HereAuthenticationError, HereUnavailableError) as exc:
            if on_result:
                on_result(idx, None, str(exc))
            raise
        except Exception as exc:
            failures.append({"indice_evento":idx,"url_post":event.get("url_post"),"erro":repr(exc)})
            if on_result: on_result(idx,None,repr(exc))
        else:
            if result:
                result["_source_index"]=idx; located.append(result)
                if on_result: on_result(idx,result,None)
            else:
                error="Localização sem resultado; nova tentativa pendente."
                failures.append({"indice_evento":idx,"url_post":event.get("url_post"),"erro":error})
                if on_result: on_result(idx,None,error)
        if progress: progress(n/total, f"Geocodificando eventos: {n}/{len(events)}")
    geo=pd.DataFrame(located)
    base=events.assign(id=range(1,len(events)+1)).copy()
    if not geo.empty:
        geo=geo.set_index("_source_index")
        base=base.join(geo,how="left",rsuffix="_localizado")
    return base, pd.DataFrame(failures)
