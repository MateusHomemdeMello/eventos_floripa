from pathlib import Path
import streamlit as st
import streamlit.components.v1 as components
from app.models.config import AppConfig, Secrets
from app.controllers.pipeline_controller import PipelineController
from dataclasses import asdict
from app.services.config_service import parse_config, export_config
from app.services.geocoding_service import validate_here_key, HereAuthenticationError
from app.services.export_service import render_webgis, export_all, events_for_webgis
from app.services.history_service import PostHistory
from app.models.results import PipelineResult
import pandas as pd


def _import_config():
    try:
        uploaded=st.session_state.get('config_file')
        text=uploaded.getvalue().decode('utf-8-sig') if uploaded else st.session_state.get('config_text','')
        config=parse_config(text)
        st.session_state['config_values']=asdict(config)
        for name,value in asdict(config).items():
            st.session_state['cfg_'+name]='\n'.join(value) if name=='profiles' else value
        st.session_state['config_message']='Configuração importada.'
    except (ValueError, UnicodeError) as exc:
        st.session_state['config_message']=str(exc)

def _secret(name):
    try: return st.secrets.get(name,"")
    except Exception: return ""

def render(root: Path):
    st.set_page_config(page_title="Qual a Boa Floripa",page_icon="🗺️",layout="wide")
    st.title("Qual a Boa Floripa")
    st.caption("Instagram → Apify → IA → HERE → CSV / Excel / WebGIS")
    history_path=root/'data/banco_posts.csv'
    if 'history_loaded' not in st.session_state:
        try:
            with PostHistory.transaction(history_path) as history:
                history.migrate_export(root/'data/output/eventos_processados.csv')
                if history.entries:
                    saved_posts,saved_events,saved_final=history.frames()
                    empty=pd.DataFrame()
                    paths=export_all(saved_final,saved_posts,empty,empty,root/'data/output',root/'assets/webgis_template.html')
                    st.session_state['result']=PipelineResult(saved_posts,saved_events,saved_final,empty,empty,*paths)
                    st.session_state['downloads']={path.name:path.read_bytes() for path in paths}
                    st.session_state['stages']={'posts':saved_posts,'events':saved_events,'final':saved_final}
            st.session_state['history_loaded']=True
        except (RuntimeError,OSError) as exc:
            st.error(f'Não foi possível carregar o histórico: {exc}')
            st.stop()
    with st.sidebar:
        st.header("Configuração")
        with st.expander('Importar configurações JSON'):
            st.file_uploader('Arquivo JSON',type=['json'],key='config_file')
            st.text_area('Ou cole o JSON',key='config_text')
            st.caption('Se houver um arquivo selecionado, ele terá prioridade sobre o texto.')
            st.button('Aplicar JSON',on_click=_import_config)
            if st.session_state.get('config_message'): st.info(st.session_state['config_message'])
        values=st.session_state.setdefault('config_values',asdict(AppConfig()))
        for name,value in values.items():
            st.session_state.setdefault('cfg_'+name,'\n'.join(value) if name=='profiles' else value)
        profiles_text=st.text_area("Perfis do Instagram (1 por linha)",height=120,key='cfg_profiles')
        days=st.number_input("Dias retroativos",1,90,key='cfg_days_back'); limit=st.number_input("Posts por execução",1,200,key='cfg_results_limit'); model=st.text_input("Modelo OpenAI",key='cfg_ai_model')
        st.subheader("Localização")
        city=st.text_input("Cidade",key='cfg_reference_city'); state=st.text_input("Estado",key='cfg_reference_state'); radius=st.number_input("Raio máximo (km)",1.0,500.0,key='cfg_max_radius_km')
        st.subheader("Credenciais")
        openai=st.text_input("OPENAI_API_KEY",value=_secret("OPENAI_API_KEY"),type="password")
        apify=st.text_input("APIFY_API_TOKEN",value=_secret("APIFY_API_TOKEN"),type="password")
        here=st.text_input("HERE_API_KEY",value=_secret("HERE_API_KEY"),type="password")
    values.update(profiles=[p.strip() for p in profiles_text.splitlines() if p.strip()],days_back=int(days),results_limit=int(limit),ai_model=model,reference_city=city,reference_state=state,max_radius_km=float(radius))
    config=AppConfig(**values)
    with st.sidebar:
        st.download_button('Exportar configurações JSON',export_config(config),file_name='configuracoes_floripa.json',mime='application/json')
        st.caption('O JSON inclui os parâmetros de processamento. As credenciais ficam separadas.')
        if st.button('Testar conexão HERE'):
            try:
                validate_here_key(here,config)
                st.success('HERE: conexão autorizada.')
            except RuntimeError as exc: st.error(str(exc))
    tab1,tab2,tab3=st.tabs(["Processamento","Resultados","WebGIS"])
    with tab1:
        st.caption('O banco local é consultado após a coleta. Posts já analisados reutilizam os resultados; somente etapas pendentes são executadas.')
        st.info("As chaves digitadas são usadas apenas na sessão. Para GitHub/Streamlit Community Cloud, prefira Secrets.")
        start=st.button("Iniciar processamento",type="primary",use_container_width=True)
        status=st.empty()
        bar=st.progress(0.0)
        stages=st.session_state.setdefault('stages', {})
        slots={}
        for name,label in [('posts','1. Apify — posts coletados'),('events','2. OpenAI — eventos extraídos'),('final','3. HERE — eventos e localidades')]:
            st.subheader(label)
            slots[name]=st.empty()
            if name in stages: slots[name].dataframe(stages[name],use_container_width=True)
            else: slots[name].info('Aguardando esta etapa.')
        def publish(name, frame):
            stages[name]=frame
            slots[name].dataframe(frame,use_container_width=True)
        if start:
            if not all([openai,apify,here]): st.error("Informe as três credenciais.")
            elif not config.profiles: st.error("Informe pelo menos um perfil.")
            else:
                controller=PipelineController(config,Secrets(openai,apify,here),root)
                try:
                    st.session_state.pop('result', None)
                    st.session_state.pop('downloads', None)
                    stages.clear()
                    for slot in slots.values(): slot.info('Aguardando esta etapa.')
                    result=controller.run(status=status.info,progress=lambda value,msg:(bar.progress(min(max(value,0.0),1.0)),status.info(msg)),on_stage=publish)
                    st.session_state['downloads']={path.name:path.read_bytes() for path in (result.csv_path,result.xlsx_path,result.webgis_path)}
                    st.session_state['result']=result; bar.progress(1.0); status.success("Processamento concluído.")
                except HereAuthenticationError as exc: status.error(str(exc))
                except Exception as exc: status.error(f"Falha: {exc}"); st.exception(exc)
    result=st.session_state.get('result')
    if result and 'downloads' not in st.session_state:
        st.session_state['downloads']={path.name:path.read_bytes() for path in (result.csv_path,result.xlsx_path,result.webgis_path)}
    if result:
        st.session_state['downloads'][result.webgis_path.name]=render_webgis(result.final,root/'assets/webgis_template.html').encode('utf-8')
    with tab2:
        if history_path.exists():
            st.download_button('Baixar banco de posts CSV',history_path.read_bytes(),file_name=history_path.name,mime='text/csv')
        if not result: st.warning("Execute o processamento primeiro.")
        else:
            c1,c2,c3=st.columns(3); c1.metric("Posts",len(result.posts)); c2.metric("Eventos",len(result.events)); c3.metric("Revisões",int(result.final.get('necessita_revisao',False).fillna(False).sum()) if 'necessita_revisao' in result.final else 0)
            st.caption('Histórico acumulado: inclui eventos encerrados e localidades pendentes. O WebGIS filtra os eventos encerrados.')
            st.dataframe(result.final,use_container_width=True,height=440)
            for label,path,mime in [("Baixar CSV",result.csv_path,"text/csv"),("Baixar Excel",result.xlsx_path,"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),("Baixar WebGIS",result.webgis_path,"text/html")]:
                st.download_button(label,st.session_state['downloads'][path.name],file_name=path.name,mime=mime)
            if not result.extraction_failures.empty:
                with st.expander("Falhas de extração"): st.dataframe(result.extraction_failures,use_container_width=True)
            if not result.location_failures.empty:
                with st.expander("Falhas de localização"): st.dataframe(result.location_failures,use_container_width=True)
    with tab3:
        if result:
            html=st.session_state['downloads'][result.webgis_path.name]
            st.caption(f'{len(events_for_webgis(result.final))} eventos com data vigente e coordenadas válidas no mapa e calendário. Eventos que terminam hoje são mantidos (America/Sao_Paulo).')
            st.download_button('Baixar mapa HTML',html,file_name=result.webgis_path.name,mime='text/html',key='download_map')
            st.caption('O HTML pode ser aberto no navegador. Os mapas base precisam de conexão com a internet.')
            components.html(html.decode('utf-8'),height=760,scrolling=False)
        else: st.warning("O WebGIS aparecerá aqui após o processamento.")
