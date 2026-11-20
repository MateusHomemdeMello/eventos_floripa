from collections.abc import Mapping

import pandas as pd
from apify_client import ApifyClient

class ApifyCollectionFailed(RuntimeError):
    pass


def _run_field(run, attribute, legacy_key):
    return run.get(legacy_key) if isinstance(run, Mapping) else getattr(run, attribute)

def _images(post: dict) -> list[str]:
    found=[]
    for key in ("images","displayUrl","imageUrl"):
        value=post.get(key)
        if isinstance(value,list): found.extend(x for x in value if isinstance(x,str))
        elif isinstance(value,str): found.append(value)
    for key in ("childPosts","children","carouselMedia"):
        for child in post.get(key) or []:
            if isinstance(child,dict):
                for k in ("displayUrl","imageUrl","url"):
                    if isinstance(child.get(k),str): found.append(child[k])
    return list(dict.fromkeys(found))

def collect_posts(token: str, config, *, run_id=None, on_started=None, days_back=None) -> pd.DataFrame:
    client=ApifyClient(token)
    days_back = config.days_back if days_back is None else days_back
    if not run_id:
        run_input={"resultsType":"posts","directUrls":config.profiles,"resultsLimit":config.results_limit,"onlyPostsNewerThan":f"{days_back} days","addParentData":False}
        started = client.actor(config.apify_actor_id).start(run_input=run_input)
        run_id = _run_field(started, 'id', 'id')
        if on_started:
            on_started(run_id)
    run = client.run(run_id).wait_for_finish()
    if not run:
        raise RuntimeError(f'Execução Apify {run_id} indisponível. Confira a conta e retome a coleta.')
    status = _run_field(run, 'status', 'status')
    if status not in {'SUCCEEDED', 'FAILED', 'TIMED-OUT', 'ABORTED'}:
        raise RuntimeError(f'Execução Apify {run_id} ainda em andamento. Retome a coleta.')
    dataset_id = _run_field(run, 'default_dataset_id', 'defaultDatasetId')
    raw = client.dataset(dataset_id).iterate_items() if dataset_id else ()
    started_at = pd.to_datetime(_run_field(run, 'started_at', 'startedAt'), utc=True)
    limit=started_at-pd.Timedelta(days=days_back)
    rows=[]
    for post in raw:
        published=pd.to_datetime(post.get("timestamp") or post.get("takenAt"),utc=True,errors="coerce")
        if pd.isna(published) or published < limit: continue
        short=post.get("shortCode") or post.get("shortcode")
        url=post.get("url") or (f"https://www.instagram.com/p/{short}/" if short else None)
        rows.append({"perfil":post.get("ownerUsername") or post.get("username"),"data_publicacao":published,"legenda":post.get("caption") or "","tipo":post.get("type") or post.get("productType"),"shortcode":short,"url_post":url,"imagens":_images(post)})
    df=pd.DataFrame(rows,columns=["perfil","data_publicacao","legenda","tipo","shortcode","url_post","imagens"])
    if not df.empty: df=df.drop_duplicates(subset=["url_post","shortcode"]).sort_values("data_publicacao",ascending=False).reset_index(drop=True)
    if status != 'SUCCEEDED':
        message = f'Execução Apify {run_id} terminou com status {status}.'
        if df.empty:
            raise ApifyCollectionFailed(message)
        df.attrs['collection_warning'] = message + ' Os posts disponíveis foram salvos como coleta parcial.'
    elif not dataset_id:
        raise ApifyCollectionFailed(f'A execução Apify {run_id} não retornou Dataset.')
    return df
