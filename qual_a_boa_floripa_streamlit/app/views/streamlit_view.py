from pathlib import Path
import streamlit as st
import streamlit.components.v1 as components
from app.models.config import AppConfig, Secrets
from app.controllers.pipeline_controller import PipelineController

def _secret(name):
    try: return st.secrets.get(name,"")
    except Exception: return ""

def render(root: Path):
    st.set_page_config(page_title="Qual a Boa Floripa",page_icon="🗺️",layout="wide")
    st.title("Qual a Boa Floripa")
    st.caption("Instagram → Apify → IA → HERE → CSV / Excel / WebGIS")
    with st.sidebar:
        st.header("Configuração")
        profiles_text=st.text_area("Perfis do Instagram (1 por linha)","https://www.instagram.com/floripa.cultural/",height=120)
        days=st.number_input("Dias retroativos",1,90,14); limit=st.number_input("Posts por execução",1,200,20); model=st.text_input("Modelo OpenAI","gpt-5.4-mini")
        st.subheader("Localização")
        city=st.text_input("Cidade","Florianópolis"); state=st.text_input("Estado","Santa Catarina"); radius=st.number_input("Raio máximo (km)",1.0,500.0,90.0)
        st.subheader("Credenciais")
        openai=st.text_input("OPENAI_API_KEY",value=_secret("OPENAI_API_KEY"),type="password")
        apify=st.text_input("APIFY_API_TOKEN",value=_secret("APIFY_API_TOKEN"),type="password")
        here=st.text_input("HERE_API_KEY",value=_secret("HERE_API_KEY"),type="password")
    config=AppConfig(profiles=[p.strip() for p in profiles_text.splitlines() if p.strip()],days_back=int(days),results_limit=int(limit),ai_model=model,reference_city=city,reference_state=state,max_radius_km=float(radius))
    tab1,tab2,tab3=st.tabs(["Processamento","Resultados","WebGIS"])
    with tab1:
        st.info("As chaves digitadas são usadas apenas na sessão. Para GitHub/Streamlit Community Cloud, prefira Secrets.")
        if st.button("Iniciar processamento",type="primary",use_container_width=True):
            if not all([openai,apify,here]): st.error("Informe as três credenciais.")
            elif not config.profiles: st.error("Informe pelo menos um perfil.")
            else:
                status=st.empty(); bar=st.progress(0.0)
                controller=PipelineController(config,Secrets(openai,apify,here),root)
                try:
                    result=controller.run(status=status.info,progress=lambda value,msg:(bar.progress(min(max(value,0.0),1.0)),status.info(msg)))
                    st.session_state['result']=result; bar.progress(1.0); status.success("Processamento concluído.")
                except Exception as exc: status.error(f"Falha: {exc}"); st.exception(exc)
    result=st.session_state.get('result')
    with tab2:
        if not result: st.warning("Execute o processamento primeiro.")
        else:
            c1,c2,c3=st.columns(3); c1.metric("Posts",len(result.posts)); c2.metric("Eventos",len(result.events)); c3.metric("Revisões",int(result.final.get('necessita_revisao',False).fillna(False).sum()) if 'necessita_revisao' in result.final else 0)
            st.dataframe(result.final,use_container_width=True,height=440)
            for label,path,mime in [("Baixar CSV",result.csv_path,"text/csv"),("Baixar Excel",result.xlsx_path,"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),("Baixar WebGIS",result.webgis_path,"text/html")]:
                st.download_button(label,path.read_bytes(),file_name=path.name,mime=mime)
            if not result.extraction_failures.empty:
                with st.expander("Falhas de extração"): st.dataframe(result.extraction_failures,use_container_width=True)
            if not result.location_failures.empty:
                with st.expander("Falhas de localização"): st.dataframe(result.location_failures,use_container_width=True)
    with tab3:
        if result and result.webgis_path.exists(): components.html(result.webgis_path.read_text(encoding='utf-8'),height=760,scrolling=False)
        else: st.warning("O WebGIS aparecerá aqui após o processamento.")
