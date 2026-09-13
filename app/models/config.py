from dataclasses import dataclass, field

@dataclass
class AppConfig:
    profiles: list[str] = field(default_factory=lambda: ["https://www.instagram.com/floripa.cultural/"])
    apify_actor_id: str = "shu8hvrXbJbY3Eb9W"
    days_back: int = 14
    results_limit: int = 20
    ai_model: str = "gpt-5.4-mini"
    max_images_per_post: int = 10
    max_image_side: int = 1600
    jpeg_quality: int = 82
    ai_interval_seconds: float = 0.2
    reference_city: str = "Florianópolis"
    reference_state: str = "Santa Catarina"
    reference_country: str = "Brasil"
    country_code: str = "BRA"
    reference_lat: float = -27.5949
    reference_lon: float = -48.5482
    max_radius_km: float = 90.0
    here_candidate_limit: int = 5
    here_language: str = "pt-BR"
    here_timeout: int = 30
    min_location_confidence: float = 0.65
    min_candidate_difference: float = 0.08
    use_web_fallback: bool = True

@dataclass
class Secrets:
    openai_api_key: str
    apify_api_token: str
    here_api_key: str
