from pathlib import Path
from datetime import datetime, timezone
import pandas as pd
from app.models.results import PipelineResult
from app.services.instagram_service import collect_posts
from app.services.ai_service import extract_events
from app.services.geocoding_service import geocode_events
from app.services.export_service import export_all
from app.services.history_service import PostHistory, post_key, records

class PipelineController:
    def __init__(self, config, secrets, root: Path): self.config=config; self.secrets=secrets; self.root=root
    def run(self, status=None, progress=None, on_stage=None):
        def say(msg):
            if status: status(msg)
        def publish(name, frame):
            if on_stage: on_stage(name, frame.copy(deep=True))
        with PostHistory.transaction(self.root/'data/banco_posts.csv') as history:
            history.migrate_export(self.root/'data/output/eventos_processados.csv')
            say('Coletando publicações na Apify...')
            posts = collect_posts(self.secrets.apify_api_token, self.config).copy()
            keys = posts.apply(post_key, axis=1) if not posts.empty else pd.Series(dtype=object)
            if keys.isna().any():
                raise RuntimeError('A coleta retornou posts sem shortcode ou URL válida do Instagram. Não foi iniciada a análise paga.')
            posts['ja_analisado'] = keys.isin(history.entries)
            publish('posts', posts)
            fresh = posts.loc[~posts['ja_analisado']].copy()
            if not fresh.empty:
                fresh['_post_key'] = fresh.apply(post_key, axis=1)
                fresh = fresh.drop_duplicates('_post_key').drop(columns=['_post_key','ja_analisado'])
            say(f'{len(posts)} posts coletados; {int(posts["ja_analisado"].sum())} já analisados; {len(fresh)} novos para GPT.')
            extraction_failures = pd.DataFrame()
            if not fresh.empty:
                extracted, extraction_failures = extract_events(self.secrets.openai_api_key, fresh, self.config, progress)
                failed_indices = set(extraction_failures.get('indice_post', []))
                failed_keys = {post_key(row) for row in records(extraction_failures)}
                event_rows = records(extracted)
                for index, post in fresh.iterrows():
                    key = post_key(post)
                    if index in failed_indices or key in failed_keys: continue
                    matches = [event for event in event_rows if post_key(event) == key]
                    history.add_extraction(key, records(pd.DataFrame([post]))[0], matches)
                history.save()
            _, events, _ = history.frames()
            publish('events', events)
            pending, references = history.pending()
            location_failures = pd.DataFrame()
            if not pending.empty:
                say(f'{len(pending)} eventos com localização pendente. Consultando HERE...')
                located, location_failures = geocode_events(pending, self.secrets.here_api_key, self.secrets.openai_api_key, self.config, progress)
                failed_indices = set(location_failures.get('indice_evento', []))
                for index, row in located.iterrows():
                    if index in failed_indices: continue
                    key, event_index = references[index]
                    history.entries[key]['final'][event_index] = records(pd.DataFrame([row]))[0]
                    history.entries[key]['updated_at'] = datetime.now(timezone.utc).isoformat()
            history.save()
            _, events, final = history.frames()
            publish('final', final)
            say('Banco atualizado. Exportando histórico e WebGIS com eventos vigentes...')
            csv, xlsx, webgis = export_all(final, posts, extraction_failures, location_failures, self.root/'data/output', self.root/'assets/webgis_template.html')
            say('Processamento concluído.')
            return PipelineResult(posts, events, final, extraction_failures, location_failures, csv, xlsx, webgis)
