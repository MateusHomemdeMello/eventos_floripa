from app.services.export_service import hhmm, date_iso, normalized_category
from app.services.export_service import events_for_webgis
import pandas as pd
import pytest
from datetime import date

def test_hhmm():
    assert hhmm("19h30") == "19:30"
    assert hhmm("19 horas") == "19:00"

def test_date_iso():
    assert date_iso("2026-09-13") == "2026-09-13"


@pytest.mark.parametrize('value',[None,'','19:99','25:00','19:30:59','das 19 às 22','2026-09-13','a confirmar'])
def test_invalid_time_is_not_guessed(value):
    assert hhmm(value)==''


@pytest.mark.parametrize('start,end,expected',[(None,None,(None,None)),('19h30',None,('19:30',None)),(None,'22h',(None,'22:00')),('00:00','23:59',('00:00','23:59'))])
def test_export_preserves_only_known_times(start,end,expected):
    frame=pd.DataFrame([{'data_inicio':'2026-09-13','latitude':-27.6,'longitude':-48.5,'horario_inicio':start,'horario_fim':end}])
    event=events_for_webgis(frame,date(2026,9,13))[0]
    assert (event['hora_inicio'],event['hora_fim'])==expected


@pytest.mark.parametrize('old,new',[
    ('feira','feiras'), ('esporte','esportes'), ('cinema','cinema_audiovisual'),
])
def test_legacy_categories_are_migrated(old,new):
    assert normalized_category(old)==new


@pytest.mark.parametrize('name,expected',[
    ('Oficina de aquarela','cursos_oficinas'),
    ('Exposição Ressonâncias','exposicoes'),
    ('Festival literário','festivais'),
    ('Espetáculo teatral','apresentacoes'),
])
def test_old_cultura_is_reclassified_by_event_focus(name,expected):
    assert normalized_category('cultura',name)==expected
