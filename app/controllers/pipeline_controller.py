import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from app.models.results import PipelineResult
from app.services.instagram_service import collect_posts
from app.services.ai_service import extract_events
from app.services.geocoding_service import geocode_events
from app.services.export_service import export_all
from app.services.history_service import PostHistory, post_key, records


class PipelineController:
    def __init__(self, config, secrets, root: Path):
        self.config=config; self.secrets=secrets; self.root=root

    @property
    def history_path(self): return self.root/'data/banco_posts.csv'

    @property
    def collected_path(self): return self.root/'data/posts_coletados.json'

    @property
    def extraction_failures_path(self): return self.root/'data/falhas_extracao.json'

    @property
    def location_failures_path(self): return self.root/'data/falhas_localizacao.json'

    def _save_frame(self,path,frame):
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(records(frame),ensure_ascii=False,allow_nan=False),encoding='utf-8')

    def _load_frame(self,path):
        if not path.exists(): return pd.DataFrame()
        return pd.DataFrame(json.loads(path.read_text(encoding='utf-8')))

    def _load_collected(self):
        if not self.collected_path.exists():
            raise RuntimeError('Nenhuma coleta salva. Execute primeiro a etapa 1 — Apify.')
        return pd.DataFrame(json.loads(self.collected_path.read_text(encoding='utf-8')))

    def collect_stage(self, status=None):
        if status: status('Coletando publicações na Apify...')
        posts=collect_posts(self.secrets.apify_api_token,self.config).copy()
        keys=posts.apply(post_key,axis=1) if not posts.empty else pd.Series(dtype=object)
        if keys.isna().any():
            raise RuntimeError('A coleta retornou posts sem shortcode ou URL válida do Instagram.')
        self.collected_path.parent.mkdir(parents=True,exist_ok=True)
        self.collected_path.write_text(json.dumps(records(posts),ensure_ascii=False,allow_nan=False),encoding='utf-8')
        with PostHistory.transaction(self.history_path) as history:
            posts['ja_analisado']=keys.isin(history.entries)
        if status: status(f'{len(posts)} posts coletados e salvos.')
        return posts

    def extraction_stage(self, posts=None, status=None, progress=None, on_saved=None):
        posts=self._load_collected() if posts is None else posts.copy()
        with PostHistory.transaction(self.history_path) as history:
            keys=posts.apply(post_key,axis=1) if not posts.empty else pd.Series(dtype=object)
            posts['ja_analisado']=keys.isin(history.entries)
            fresh=posts.loc[~posts['ja_analisado']].copy()
            if not fresh.empty:
                fresh['_post_key']=fresh.apply(post_key,axis=1)
                fresh=fresh.drop_duplicates('_post_key').drop(columns=['_post_key','ja_analisado'])
            if status: status(f'{len(posts)} posts; {int(posts["ja_analisado"].sum())} já analisados; {len(fresh)} novos para GPT.')
            failures=pd.DataFrame()
            if not fresh.empty:
                self._save_frame(self.extraction_failures_path,pd.DataFrame())

                def persist(index,post,found,error):
                    if error:
                        current=self._load_frame(self.extraction_failures_path)
                        row=pd.DataFrame([{'indice_post':index,'url_post':post.get('url_post'),'erro':error}])
                        self._save_frame(self.extraction_failures_path,pd.concat([current,row],ignore_index=True))
                        return
                    key=post_key(post)
                    history.add_extraction(key,records(pd.DataFrame([post]))[0],found or [])
                    history.save()
                    if on_saved: on_saved(history.frames()[1])

                extracted,failures=extract_events(self.secrets.openai_api_key,fresh,self.config,progress,persist)
                failed_indices=set(failures.get('indice_post',[]))
                failed_keys={post_key(row) for row in records(failures)}
                event_rows=records(extracted)
                for index,post in fresh.iterrows():
                    key=post_key(post)
                    if index in failed_indices or key in failed_keys: continue
                    matches=[event for event in event_rows if post_key(event)==key]
                    if key not in history.entries:
                        history.add_extraction(key,records(pd.DataFrame([post]))[0],matches)
                history.save()
            _,events,_=history.frames()
        self._save_frame(self.extraction_failures_path,failures)
        if status: status(f'Extração concluída: {len(events)} eventos no banco.')
        return posts,events,failures

    def location_stage(self, status=None, progress=None, on_saved=None):
        self._save_frame(self.location_failures_path,pd.DataFrame())
        with PostHistory.transaction(self.history_path) as history:
            pending,references=history.pending()
            if pending.empty:
                if status: status('Não há localizações pendentes.')
                return history.frames()[2],pd.DataFrame()
            if status: status(f'{len(pending)} eventos pendentes. Consultando HERE...')

            def persist(index,result,error):
                if result is None:
                    current=self._load_frame(self.location_failures_path)
                    row=pd.DataFrame([{'indice_evento':index,'url_post':pending.loc[index].get('url_post'),'erro':error}])
                    self._save_frame(self.location_failures_path,pd.concat([current,row],ignore_index=True))
                    return
                key,event_index=references[index]
                saved=dict(result); saved.pop('_source_index',None)
                saved={**history.entries[key]['events'][int(event_index)],**saved}
                history.entries[key]['final'][event_index]=saved
                history.entries[key]['updated_at']=datetime.now(timezone.utc).isoformat()
                history.save()
                if on_saved: on_saved(history.frames()[2])

            located,failures=geocode_events(pending,self.secrets.here_api_key,self.secrets.openai_api_key,self.config,progress,persist)
            failed_indices=set(failures.get('indice_evento',[]))
            for index,row in located.iterrows():
                if index in failed_indices: continue
                key,event_index=references[index]
                if event_index in history.entries[key]['final']: continue
                saved=records(pd.DataFrame([row]))[0]
                saved={**history.entries[key]['events'][int(event_index)],**saved}
                history.entries[key]['final'][event_index]=saved
                history.entries[key]['updated_at']=datetime.now(timezone.utc).isoformat()
                history.save()
            final=history.frames()[2]
        self._save_frame(self.location_failures_path,failures)
        if status: status(f'Localização concluída: {len(failures)} falhas pendentes.')
        return final,failures

    def export_stage(self, posts=None, extraction_failures=None, location_failures=None, status=None):
        with PostHistory.transaction(self.history_path) as history:
            saved_posts,events,final=history.frames()
        if posts is None:
            try: posts=self._load_collected()
            except RuntimeError: posts=saved_posts
        extraction_failures=extraction_failures if extraction_failures is not None else self._load_frame(self.extraction_failures_path)
        location_failures=location_failures if location_failures is not None else self._load_frame(self.location_failures_path)
        if status: status('Exportando banco, WebGIS e interface...')
        paths=export_all(final,posts,extraction_failures,location_failures,self.root/'data/output',self.root/'assets/webgis_template.html')
        return PipelineResult(posts,events,final,extraction_failures,location_failures,*paths)

    def restore_state(self):
        with PostHistory.transaction(self.history_path) as history:
            history.migrate_export(self.root/'data/output/eventos_processados.csv')
            saved_posts,events,final=history.frames()
        try: collected=self._load_collected()
        except RuntimeError: collected=saved_posts
        stages={}
        if not collected.empty: stages['posts']=collected
        if not events.empty: stages['events']=events
        if not final.empty: stages['final']=final
        result=self.export_stage(collected) if not events.empty else None
        return stages,result

    def set_publication(self, changes):
        with PostHistory.transaction(self.history_path) as history:
            for post_id,event_index,publish in changes:
                history.set_publication(post_id,event_index,publish)
            history.save()
        return self.export_stage()

    def run(self, status=None, progress=None, on_stage=None):
        posts=self.collect_stage(status)
        if on_stage: on_stage('posts',posts.copy(deep=True))
        posts,events,extraction_failures=self.extraction_stage(posts,status,progress,lambda frame: on_stage('events',frame.copy(deep=True)) if on_stage else None)
        if on_stage: on_stage('events',events.copy(deep=True))
        final,location_failures=self.location_stage(status,progress,lambda frame: on_stage('final',frame.copy(deep=True)) if on_stage else None)
        if on_stage: on_stage('final',final.copy(deep=True))
        result=self.export_stage(posts,extraction_failures,location_failures,status)
        if status: status('Processamento concluído.')
        return result
