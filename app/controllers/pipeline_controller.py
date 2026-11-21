import json
from hashlib import sha256
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pandas as pd

from app.models.results import PipelineResult
from app.services.instagram_service import ApifyCollectionFailed, collect_posts
from app.services.ai_service import EXTRACTION_VERSION, extract_events
from app.services.geocoding_service import geocode_events
from app.services.export_service import consolidate_extractions, export_all
from app.services.history_service import PostHistory, post_key, records
from app.services.location_validation import location_plan
from app.services.event_identity import normalized
from app.services.spatial_quality import AUTO, CONFLICT, MANUAL, REOPENED, REVIEW, coordinate, has_position
from app.services.venue_registry import lookup_venue, registry_entry
from app.services.survey_service import STAGE_FILES, append_survey, stage_time, with_dates
from app.utils.atomic_file import write_text


class PipelineController:
    def __init__(self, config, secrets, root: Path):
        self.config=config; self.secrets=secrets; self.root=root
        self.output_warnings = []

    @property
    def history_path(self): return self.root/'data/banco_posts.csv'

    @property
    def collected_path(self): return self.root/'data/posts_coletados.json'

    @property
    def extraction_failures_path(self): return self.root/'data/falhas_extracao.json'

    @property
    def location_failures_path(self): return self.root/'data/falhas_localizacao.json'

    @property
    def survey_paths(self):
        return {stage: self.root/'data/levantamentos'/name for stage,name in STAGE_FILES.items()}

    @property
    def collections_dir(self):
        return self.root/'data/coletas'

    @property
    def apify_run_path(self):
        return self.root/'data/apify_coleta_pendente.json'

    def _try_output(self, path, action):
        try:
            return action()
        except (OSError, RuntimeError, ValueError) as exc:
            message = f'{path.name}: {exc}'
            if message not in self.output_warnings:
                self.output_warnings.append(message)
            return None

    def saved_state_signature(self):
        paths=[self.history_path,self.history_path.with_suffix('.sqlite3'),self.collected_path,self.extraction_failures_path,
               self.apify_run_path,*sorted(self.collections_dir.glob('*.json')),
               self.location_failures_path,*self.survey_paths.values()]
        signatures = []
        for path in paths:
            try:
                stat = path.stat()
                signatures.append((str(path), stat.st_mtime_ns, stat.st_size))
            except FileNotFoundError:
                signatures.append((str(path), None, None))
        return str(self.root.resolve()), 'autoload-v4', tuple(signatures)

    def _sync_surveys(self, history):
        self._try_output(self.history_path, history.sync_csv)
        posts,events,final=history.frames()
        # Unlocated events belong in the UI, but not in the HERE survey yet.
        completed=[str(row['_event_index']) in history.entries[row['_post_id']]['final']
                   for row in records(final)]
        located=final.loc[completed] if len(final) else final
        for stage,frame in [('posts',posts),('events',events),('final',located)]:
            path = self.survey_paths[stage]
            self._try_output(path, lambda: append_survey(path, stage, frame))

    def _save_extraction(self, history, post, found):
        stamp=stage_time()
        source=records(pd.DataFrame([post]))[0]
        source['data_analise_ia']=stamp
        source['modelo_extracao'] = self.config.ai_model
        source['versao_prompt_extracao'] = EXTRACTION_VERSION
        content = [source.get(field) for field in ('legenda', 'imagens', 'data_publicacao')]
        source['hash_conteudo_extracao'] = sha256(json.dumps(content, ensure_ascii=False).encode()).hexdigest()
        events=[{**event,'id_coleta':source.get('id_coleta'),
                 'data_scraping':source.get('data_scraping'),
                 'data_analise_ia':stamp,'data_geolocalizacao':None} for event in (found or [])]
        history.add_extraction(post_key(post),source,events)
        history.checkpoint(post_key(post))

    def _save_frame(self,path,frame):
        write_text(path, json.dumps(records(frame), ensure_ascii=False, allow_nan=False))

    def _load_frame(self,path):
        if not path.exists(): return pd.DataFrame()
        return pd.DataFrame(json.loads(path.read_text(encoding='utf-8')))

    def _load_failures(self, path):
        frame = self._try_output(path, lambda: self._load_frame(path))
        return pd.DataFrame() if frame is None else frame

    def _record_failure(self, path, row):
        def append():
            current = self._load_frame(path)
            failures = pd.concat([current, pd.DataFrame([row])], ignore_index=True)
            self._save_frame(path, failures)

        self._try_output(path, append)

    def _load_collected(self):
        paths = sorted(self.collections_dir.glob('*.json'))
        if paths:
            return with_dates(self._load_frame(paths[-1]))
        if self.collected_path.exists():
            return with_dates(self._load_frame(self.collected_path))
        raise FileNotFoundError('Nenhuma coleta salva. Execute primeiro a etapa 1 — Apify.')

    def _collected_batches(self):
        paths = sorted(self.collections_dir.glob('*.json'), reverse=True)
        if not paths and self.collected_path.exists():
            paths.append(self.collected_path)
        return [with_dates(self._load_frame(path)) for path in paths]

    def _extraction_queue(self):
        batches = self._collected_batches()
        if not batches:
            return self._load_collected()
        posts = pd.concat(batches, ignore_index=True)
        if posts.empty:
            return posts
        return posts.loc[~posts.apply(post_key, axis=1).duplicated()].reset_index(drop=True)

    def _sync_collections(self):
        path = self.survey_paths['posts']
        batches = self._collected_batches()
        if batches:
            posts = pd.concat(list(reversed(batches)), ignore_index=True)
            if not posts.empty:
                posts = posts.loc[posts.apply(post_key, axis=1).notna()]
            self._try_output(path, lambda: append_survey(path, 'posts', posts))

    def collect_stage(self, status=None):
        if status: status('Coletando publicações na Apify...')
        with PostHistory.transaction(self.history_path) as history:
            active = json.loads(self.apify_run_path.read_text(encoding='utf-8')) if self.apify_run_path.exists() else {}
            batch_id = active.get('batch_id') or stage_time().replace(':', '-') + '_' + str(uuid4())
            batch_path = self.collections_dir/f'{batch_id}.json'
            days_back = active.get('days_back', self.config.days_back)

            def save_run(run_id):
                write_text(self.apify_run_path, json.dumps({'batch_id': batch_id, 'run_id': run_id,
                                                            'days_back': days_back}))

            if batch_path.exists():
                posts = with_dates(self._load_frame(batch_path))
            else:
                if not active:
                    save_run(None)
                try:
                    posts = with_dates(collect_posts(self.secrets.apify_api_token, self.config, days_back=days_back,
                                                     run_id=active.get('run_id'), on_started=save_run))
                except ApifyCollectionFailed:
                    self.apify_run_path.unlink(missing_ok=True)
                    raise
                if posts.attrs.get('collection_warning'):
                    self.output_warnings.append(posts.attrs['collection_warning'])
                posts['data_scraping'] = stage_time()
                posts['id_coleta'] = batch_id
                keys = posts.apply(post_key, axis=1) if not posts.empty else pd.Series(dtype=object)
                posts['ja_analisado'] = keys.isin(history.entries)
                invalid = int(keys.isna().sum())
                if invalid:
                    self.output_warnings.append(f'{invalid} posts da coleta estão sem identificador válido.')
                legacy_path = self.collections_dir/'0000-legado.json'
                if self.collected_path.exists() and not legacy_path.exists() and not any(self.collections_dir.glob('*.json')):
                    self._save_frame(legacy_path, self._load_collected())
                self._save_frame(batch_path, posts)
            self._try_output(self.collected_path, lambda: self._save_frame(self.collected_path, posts))
            self.apify_run_path.unlink(missing_ok=True)
            self._sync_surveys(history)
            self._sync_collections()
        if status: status(f'{len(posts)} posts coletados e salvos.')
        return posts

    def extraction_stage(self, posts=None, status=None, progress=None, on_saved=None):
        posts=self._extraction_queue() if posts is None else with_dates(posts)
        with PostHistory.transaction(self.history_path) as history:
            keys=posts.apply(post_key,axis=1) if not posts.empty else pd.Series(dtype=object)
            posts['ja_analisado']=keys.isin(history.entries)
            fresh=posts.loc[~posts['ja_analisado'] & keys.notna()].copy()
            if not fresh.empty:
                fresh['_post_key']=fresh.apply(post_key,axis=1)
                fresh=fresh.drop_duplicates('_post_key').drop(columns=['_post_key','ja_analisado']).reset_index(drop=True)
            if status: status(f'{len(posts)} posts; {int(posts["ja_analisado"].sum())} já analisados; {len(fresh)} novos para GPT.')
            failures=pd.DataFrame()
            if not fresh.empty:
                history.prepare_database()

                def persist(index,post,found,error):
                    if error:
                        self._record_failure(self.extraction_failures_path,
                                             {'indice_post': index, 'url_post': post.get('url_post'), 'erro': error})
                        return
                    self._save_extraction(history,post,found)
                    if on_saved and index % 20 == 0:
                        on_saved(history.frames()[1])

                try:
                    extracted,failures=extract_events(self.secrets.openai_api_key,fresh,self.config,progress,persist)
                    failed_indices=set(failures.get('indice_post',[]))
                    failed_keys={post_key(row) for row in records(failures)}
                    event_rows=records(extracted)
                    for index,post in fresh.iterrows():
                        key=post_key(post)
                        if index in failed_indices or key in failed_keys or key in history.entries:
                            continue
                        matches=[event for event in event_rows if post_key(event)==key]
                        self._save_extraction(history,post,matches)
                    self._try_output(self.extraction_failures_path,
                                     lambda: self._save_frame(self.extraction_failures_path, failures))
                finally:
                    self._sync_surveys(history)
            else:
                self._sync_surveys(history)
                failures = self._load_failures(self.extraction_failures_path)
            _,events,_=history.frames()
        if status: status('Validando a tabela de extração por dia...')
        validated = consolidate_extractions(events)
        if status: status(f'Extração concluída: {len(validated)} ocorrências após a validação diária.')
        return posts,validated,failures

    def location_stage(self, status=None, progress=None, on_saved=None):
        with PostHistory.transaction(self.history_path) as history:
            queries, references, conflicts = location_plan(history)
            venues = {venue['id']: venue for venue in history.venue_records()}
            failures = []

            def save(refs, result, signature, venue=None):
                stamp = stage_time()
                previous = {}
                for key, index in refs:
                    entry = history.entries[key]
                    previous.setdefault(key, (entry['final'].copy(), entry['updated_at']))
                    prior = entry['final'].get(index, {})
                    result_fields = {**prior, **result} if result.get('status_localizacao') == CONFLICT else result
                    entry['final'][index] = {
                        **entry['events'][int(index)], **result_fields,
                        'assinatura_localizacao': signature, 'data_geolocalizacao': stamp,
                        **{field: prior[field] for field in ('historico_localizacao', 'publicar_webgis',
                                                            'correcao_localizacao') if field in prior},
                    }
                    entry['updated_at'] = datetime.now(timezone.utc).isoformat()
                try:
                    history.checkpoint_many(previous, venue)
                except Exception:
                    for key, (final, updated_at) in previous.items():
                        history.entries[key].update(final=final, updated_at=updated_at)
                    raise

            for row in conflicts.to_dict('records'):
                refs = row.pop('refs')
                signature = row.pop('signature')
                save(refs, {'status_localizacao': CONFLICT, 'necessita_revisao': True,
                            'motivo_revisao': row['erro'], 'fontes_localizacao': row['urls_publicacoes'],
                            'evidencias_fontes': row['evidencias_fontes']}, signature)
                failures.append(row)

            saved_indices = set()

            def persist(index, result, error):
                refs, links, signature = references[index]
                if result is None:
                    failure = {'indice_evento': index, 'url_post': queries.loc[index].get('url_post'),
                               'urls_publicacoes': links, 'erro': error}
                    failures.append(failure)
                    self._record_failure(self.location_failures_path, failure)
                    return
                saved = dict(result)
                saved.pop('_source_index', None)
                saved.pop('_ignorar_cadastro', None)
                if len(links) > 1 or len(refs) > 1:
                    for field in queries.columns:
                        saved.pop(field, None)
                    saved['fontes_localizacao'] = links
                if not saved.get('status_localizacao'):
                    saved['status_localizacao'] = REVIEW
                    saved['necessita_revisao'] = True
                saved['data_validacao_localizacao'] = stage_time()
                venue = None
                if (saved['status_localizacao'] == AUTO and saved.get('precisao_localizacao') == 'poi'
                        and saved.get('here_id') and not saved.get('local_cadastrado_id')):
                    previous = next((record for record in venues.values()
                                     if record['location'].get('here_id') == saved['here_id']), None)
                    if previous is None:
                        source = queries.loc[index]
                        venue = registry_entry(self._registry_location(saved),
                                               aliases=[source.get('local_informado')],
                                               parent=source.get('instituicao_local') or '')
                        saved['local_cadastrado_id'] = venue['id']
                        venues[venue['id']] = venue
                save(refs, saved, signature, venue)
                saved_indices.add(index)
                if on_saved and index % 20 == 0:
                    on_saved(history.frames()[2])

            remote_indices = []
            for index, query in queries.iterrows():
                cached = lookup_venue(query, venues.values(), self.config)
                if cached:
                    persist(index, cached, None)
                else:
                    remote_indices.append(index)

            if status:
                status(f'{len(remote_indices)} ocorrências para a HERE; {len(saved_indices)} do cadastro.')
            try:
                if remote_indices:
                    located, errors = geocode_events(queries.loc[remote_indices], self.secrets.here_api_key,
                                                     self.secrets.openai_api_key, self.config, progress, persist,
                                                     lambda event: lookup_venue(event, venues.values(), self.config))
                    failed_indices = set(errors.get('indice_evento', []))
                    reported = {row.get('indice_evento') for row in failures}
                    failures.extend(row for row in records(errors) if row.get('indice_evento') not in reported)
                    for index, row in located.iterrows():
                        if index not in failed_indices and index not in saved_indices:
                            persist(index, records(pd.DataFrame([row]))[0], None)
            finally:
                self._try_output(self.location_failures_path,
                                 lambda: self._save_frame(self.location_failures_path, pd.DataFrame(failures)))
                self._sync_surveys(history)
            return history.frames()[2], pd.DataFrame(failures)

    @staticmethod
    def _registry_location(location):
        fields = ('local_padronizado', 'endereco', 'bairro', 'municipio', 'uf', 'pais', 'latitude', 'longitude',
                  'here_id', 'precisao_localizacao', 'here_result_type', 'tipo_numero_here', 'acessos_here',
                  'status_localizacao', 'data_validacao_localizacao', 'necessita_revisao', 'origem_localizacao')
        return {field: location[field] for field in fields if field in location}

    def registered_venues(self):
        with PostHistory.transaction(self.history_path) as history:
            return [venue for venue in history.venue_records() if venue['active']]

    def review_location(self, post_id, event_index, *, action, reason, location=None,
                        venue_id=None, aliases=(), parent='', register=False):
        if not reason.strip():
            raise ValueError('Informe o motivo da revisão.')
        with PostHistory.transaction(self.history_path) as history:
            key, index = str(post_id), str(event_index)
            entry = history.entries[key]
            event = entry['events'][int(index)]
            prior = entry['final'].get(index, {})
            venues = {venue['id']: venue for venue in history.venue_records()}
            venue = venues.get(venue_id or prior.get('local_cadastrado_id'))
            stamp = stage_time()
            change = {'at': stamp, 'action': action, 'reason': reason,
                      'previous': self._registry_location(prior)}
            audit = [*prior.get('historico_localizacao', []), change]
            result = {**event, **prior, 'historico_localizacao': audit,
                      'data_validacao_localizacao': stamp, 'data_geolocalizacao': stamp}
            modified_venue = None
            if action in {'reabrir', 'invalidar_cadastro'}:
                result.update(status_localizacao=REOPENED, necessita_revisao=True, motivo_revisao=reason)
                if action == 'invalidar_cadastro':
                    if not venue or not venue['active']:
                        raise ValueError('Esta ocorrência não está vinculada a um local ativo.')
                    modified_venue = {**venue, 'active': False,
                                      'history': [*venue['history'], {'at': stamp, 'reason': reason}]}
                    affected = {}
                    for other_key, other_entry in history.entries.items():
                        for other in other_entry['final'].values():
                            if other.get('local_cadastrado_id') == venue['id'] and other.get('status_localizacao') != MANUAL:
                                other.update(status_localizacao=REOPENED, necessita_revisao=True, motivo_revisao=reason)
                                affected[other_key] = None
            elif action == 'corrigir_endereco':
                if not location or not location.get('endereco_informado', '').strip():
                    raise ValueError('Informe o endereço corrigido.')
                result['correcao_localizacao'] = {field: value for field, value in location.items() if value}
                result['correcao_localizacao'].update(evidencia_endereco=location['endereco_informado'],
                                                      origem_evidencia_endereco='revisao_manual')
                result.update(status_localizacao=REOPENED, necessita_revisao=True, motivo_revisao=reason)
            elif action in {'aprovar', 'vincular'}:
                if action == 'vincular' and (not venue or not venue['active']):
                    raise ValueError('Selecione um local ativo no cadastro.')
                point = venue['location'] if action == 'vincular' else location
                if not point or not has_position(point):
                    raise ValueError('Informe latitude e longitude válidas.')
                result.update({field: None for field in ('here_id', 'here_result_type', 'tipo_numero_here',
                                                        'acessos_here', 'local_cadastrado_id')})
                result.update(self._registry_location(point), status_localizacao=MANUAL,
                              latitude=coordinate(point.get('latitude'), 90),
                              longitude=coordinate(point.get('longitude'), 180),
                              necessita_revisao=False, motivo_revisao=None,
                              data_validacao_localizacao=stamp, origem_localizacao='revisao_manual',
                              precisao_localizacao=point.get('precisao_localizacao') or 'manual')
                if action == 'vincular':
                    result['local_cadastrado_id'] = venue['id']
                if register and action == 'aprovar':
                    if not result.get('local_padronizado'):
                        raise ValueError('Informe o nome do espaço para cadastrá-lo.')
                    if venue and (not venue['active'] or
                                  normalized(venue['name']) != normalized(result['local_padronizado']) or
                                  normalized(venue['location'].get('endereco')) != normalized(result.get('endereco'))):
                        venue = None
                    modified_venue = registry_entry(self._registry_location(result), aliases=aliases,
                                                    parent=parent, previous=venue)
                    result['local_cadastrado_id'] = modified_venue['id']
            else:
                raise ValueError('Ação de revisão desconhecida.')
            entry['final'][index] = result
            entry['updated_at'] = datetime.now(timezone.utc).isoformat()
            keys = list(affected) if action == 'invalidar_cadastro' else []
            history.checkpoint_many(dict.fromkeys([key, *keys]), modified_venue)
            self._sync_surveys(history)
        return self.export_stage()

    def export_stage(self, posts=None, extraction_failures=None, location_failures=None, status=None):
        with PostHistory.transaction(self.history_path) as history:
            self._sync_surveys(history)
            self._sync_collections()
            saved_posts,events,final=history.frames()
        if posts is None:
            try: posts=self._load_collected()
            except FileNotFoundError: posts=saved_posts
        extraction_failures=extraction_failures if extraction_failures is not None else self._load_failures(self.extraction_failures_path)
        location_failures=location_failures if location_failures is not None else self._load_failures(self.location_failures_path)
        if status: status('Gerando WebMap em um único HTML...')
        webgis=export_all(final,posts,extraction_failures,location_failures,self.root/'data/output',self.root/'assets/webgis_template.html')
        return PipelineResult(posts,consolidate_extractions(events),final,extraction_failures,location_failures,webgis)

    def restore_state(self):
        with PostHistory.transaction(self.history_path) as history:
            legacy=self.root/'data/output/eventos_processados.csv'
            if not legacy.exists():
                legacy=self.root/'data/backups/legacy_output/eventos_processados.csv'
            history.migrate_export(legacy)
            self._sync_surveys(history)
            if self.apify_run_path.exists() or self.collections_dir.exists() or self.collected_path.exists():
                self._sync_collections()
            saved_posts,events,final=history.frames()
        try: collected=self._load_collected()
        except FileNotFoundError: collected=saved_posts
        stages={}
        if not collected.empty: stages['posts']=collected
        if not events.empty: stages['events']=consolidate_extractions(events)
        if not final.empty: stages['final']=final
        result = (self._try_output(self.root/'data/output/qual_a_boa_floripa.html',
                                   lambda: self.export_stage(collected))
                  if not events.empty else None)
        # Survey CSVs are also readable without an operational bank. Display them
        # without importing them as completed work or triggering external APIs.
        for stage,path in self.survey_paths.items():
            if stage not in stages and path.exists():
                try:
                    frame=with_dates(pd.read_csv(path))
                except (OSError, ValueError) as exc:
                    self.output_warnings.append(f'{path.name}: {exc}')
                    continue
                if not frame.empty:
                    stages[stage]=consolidate_extractions(frame) if stage == 'events' else frame
        return stages,result

    def set_publication(self, changes):
        with PostHistory.transaction(self.history_path) as history:
            for post_id,event_index,publish in changes:
                history.set_publication(post_id,event_index,publish)
                history.checkpoint(str(post_id))
            self._try_output(self.history_path, history.sync_csv)
        return self.export_stage()

    def run(self, status=None, progress=None, on_stage=None, resume=False):
        if not resume or self.apify_run_path.exists():
            self.collect_stage(status)
        try:
            posts = self._extraction_queue()
        except FileNotFoundError:
            if not resume:
                raise
            posts = pd.DataFrame()
        if on_stage: on_stage('posts',posts.copy(deep=True))
        posts,events,extraction_failures=self.extraction_stage(posts,status,progress,lambda frame: on_stage('events',frame.copy(deep=True)) if on_stage else None)
        if on_stage: on_stage('events',events.copy(deep=True))
        final,location_failures=self.location_stage(status,progress,lambda frame: on_stage('final',frame.copy(deep=True)) if on_stage else None)
        if on_stage: on_stage('final',final.copy(deep=True))
        result=self.export_stage(posts,extraction_failures,location_failures,status)
        if status: status('Processamento concluído.')
        return result
