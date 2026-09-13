import pandas as pd
from apify_client import ApifyClient

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

def collect_posts(token: str, config) -> pd.DataFrame:
    client=ApifyClient(token)
    run_input={"resultsType":"posts","directUrls":config.profiles,"resultsLimit":config.results_limit,"onlyPostsNewerThan":f"{config.days_back} days","addParentData":False}
    run=client.actor(config.apify_actor_id).call(run_input=run_input)
    if not run or not run.default_dataset_id: raise RuntimeError("A execução da Apify não retornou Dataset.")
    raw=list(client.dataset(run.default_dataset_id).iterate_items())
    limit=pd.Timestamp.now(tz="UTC")-pd.Timedelta(days=config.days_back)
    rows=[]
    for post in raw:
        published=pd.to_datetime(post.get("timestamp") or post.get("takenAt"),utc=True,errors="coerce")
        if pd.isna(published) or published < limit: continue
        short=post.get("shortCode") or post.get("shortcode")
        url=post.get("url") or (f"https://www.instagram.com/p/{short}/" if short else None)
        rows.append({"perfil":post.get("ownerUsername") or post.get("username"),"data_publicacao":published,"legenda":post.get("caption") or "","tipo":post.get("type") or post.get("productType"),"shortcode":short,"url_post":url,"imagens":_images(post)})
    df=pd.DataFrame(rows,columns=["perfil","data_publicacao","legenda","tipo","shortcode","url_post","imagens"])
    if not df.empty: df=df.drop_duplicates(subset=["url_post","shortcode"]).sort_values("data_publicacao",ascending=False).reset_index(drop=True)
    return df
