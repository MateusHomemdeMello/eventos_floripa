from pathlib import Path
from app.models.results import PipelineResult
from app.services.instagram_service import collect_posts
from app.services.ai_service import extract_events
from app.services.geocoding_service import geocode_events
from app.services.export_service import export_all

class PipelineController:
    def __init__(self, config, secrets, root: Path): self.config=config; self.secrets=secrets; self.root=root
    def run(self, status=None, progress=None):
        def say(msg):
            if status: status(msg)
        say("Coletando publicações na Apify..."); posts=collect_posts(self.secrets.apify_api_token,self.config)
        say(f"{len(posts)} posts válidos. Interpretando conteúdo com IA...")
        events, extraction_failures=extract_events(self.secrets.openai_api_key,posts,self.config,progress)
        say(f"{len(events)} eventos extraídos. Localizando endereços...")
        final, location_failures=geocode_events(events,self.secrets.here_api_key,self.secrets.openai_api_key,self.config,progress)
        say("Gerando CSV, Excel e WebGIS...")
        csv,xlsx,webgis=export_all(final,posts,extraction_failures,location_failures,self.root/'data/output',self.root/'assets/webgis_template.html')
        say("Processamento concluído.")
        return PipelineResult(posts,events,final,extraction_failures,location_failures,csv,xlsx,webgis)
