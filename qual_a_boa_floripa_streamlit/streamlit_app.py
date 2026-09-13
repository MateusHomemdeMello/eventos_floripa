from pathlib import Path
from app.views.streamlit_view import render

if __name__ == "__main__":
    render(Path(__file__).resolve().parent)
