from dataclasses import dataclass
from pathlib import Path
import pandas as pd

@dataclass
class PipelineResult:
    posts: pd.DataFrame
    events: pd.DataFrame
    final: pd.DataFrame
    extraction_failures: pd.DataFrame
    location_failures: pd.DataFrame
    webgis_path: Path
