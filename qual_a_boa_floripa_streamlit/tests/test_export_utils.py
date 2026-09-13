from app.services.export_service import hhmm, date_iso

def test_hhmm():
    assert hhmm("19h30") == "19:30"
    assert hhmm("19 horas") == "19:00"

def test_date_iso():
    assert date_iso("2026-09-13") == "2026-09-13"
