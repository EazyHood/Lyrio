# -*- coding: utf-8 -*-
"""
Fuentes de letras verificadas empiricamente el 2026-07-23 desde este PC.
Solo requests puro (sin navegador, sin Cloudflare bypass).

Fuentes:
  1. letras_com(query)   -> letra PLANA (la mejor para espanol). Busqueda solr + scrape.
  2. qq_music(query)     -> LRC SINCRONIZADO (base64). Requiere Referer y.qq.com.
  3. kugou(query)        -> LRC SINCRONIZADO. songsearch -> krcs (hash) -> download (base64).
  4. genius_dumb(query)  -> letra PLANA via frontend dumb (proxy de Genius sin Cloudflare).
  5. megalobiz(query)    -> LRC SINCRONIZADO. Scrape directo. Servidor flaky: reintentar 500/503.
"""
import requests, re, json, base64, time, html as H

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
STOPWORDS = {"de", "la", "el", "los", "las", "del", "un", "una", "y", "a", "en"}


# ---------------------------------------------------------------- 1. LETRAS.COM
def letras_com(query):
    """Letra plana. Busqueda: solr.sscdn.co/letras/m1/ (JSONP). Fallback sin stopwords."""
    def _search(q):
        r = requests.get("https://solr.sscdn.co/letras/m1/", params={"q": q}, headers=UA, timeout=12)
        m = re.match(r"^\w+\((.*)\)\s*;?\s*$", r.text.strip(), re.S)
        return json.loads(m.group(1) if m else r.text)["response"]["docs"]

    # Solr de letras.com pondera mal las queries con stopwords ("de la el"):
    # buscar con y sin stopwords, unir y elegir por solapamiento de palabras.
    q2 = " ".join(w for w in query.split() if w.lower() not in STOPWORDS)
    docs = _search(query)
    if q2 and q2 != query:
        seen = {d.get("id") for d in docs}
        docs += [d for d in _search(q2) if d.get("id") not in seen]
    if not docs:
        return None
    qwords = {w.lower() for w in query.split() if w.lower() not in STOPWORDS}

    def score(d):
        hitwords = {w.lower() for w in re.findall(r"\w+", d.get("art", "") + " " + d.get("txt", ""))}
        return len(qwords & hitwords)

    d = max(docs, key=score)
    if score(d) == 0:
        return None
    url = f"https://www.letras.com/{d['dns']}/{d.get('url') or d['id'].replace('mus','')}/"
    r = requests.get(url, headers=UA, timeout=12)
    m = re.search(r'<div[^>]*class="lyric-original"[^>]*>(.*?)</div>', r.text, re.S)
    if not m:
        return None
    block = m.group(1)
    paras = re.findall(r"<p>(.*?)</p>", block, re.S)
    out = []
    for p in paras:
        p = re.sub(r"<br\s*/?>", "\n", p)
        p = re.sub(r"<[^>]+>", "", p)
        out.append(H.unescape(p).strip())
    return {"source": "letras.com", "artist": d.get("art"), "title": d.get("txt"),
            "url": url, "synced": False, "lyrics": "\n\n".join(out)}


# ---------------------------------------------------------------- 2. QQ MUSIC
def qq_music(query):
    """LRC sincronizado. Busqueda soso + fcg_query_lyric_new.fcg (base64)."""
    h = dict(UA, Referer="https://y.qq.com/")
    r = requests.get("https://c.y.qq.com/soso/fcgi-bin/client_search_cp",
                     params={"w": query, "p": 1, "n": 5, "format": "json", "cr": 1, "t": 0},
                     headers=h, timeout=15)
    txt = re.sub(r"^callback\(|\)$", "", r.text.strip())
    songs = json.loads(txt).get("data", {}).get("song", {}).get("list", [])
    if not songs:
        return None
    s = songs[0]
    r2 = requests.get("https://c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg",
                      params={"songmid": s["songmid"], "format": "json", "nobase64": 0, "g_tk": 5381},
                      headers=h, timeout=15)
    t2 = re.sub(r"^MusicJsonCallback\(|\)$", "", r2.text.strip())
    d2 = json.loads(t2)
    if not d2.get("lyric"):
        return None  # retcode -1901 = sin letra
    lrc = base64.b64decode(d2["lyric"]).decode("utf-8", errors="replace")
    singers = ",".join(x.get("name", "") for x in s.get("singer", []))
    return {"source": "qq_music", "artist": singers, "title": s.get("songname"),
            "songmid": s["songmid"], "synced": True, "lyrics": lrc}


# ---------------------------------------------------------------- 3. KUGOU
def kugou(query):
    """LRC sincronizado. OJO: usar songsearch (no mobilecdn, lleno de covers falsos)
    y filtrar el primer resultado cuyo SingerName aparezca en la query."""
    r = requests.get("http://songsearch.kugou.com/song_search_v2",
                     params={"keyword": query, "page": 1, "pagesize": 10, "platform": "WebFilter"},
                     headers=UA, timeout=15)
    lst = r.json().get("data", {}).get("lists", [])
    if not lst:
        return None
    qlow = query.lower()
    pick = None
    for x in lst:
        singer = (x.get("SingerName") or "").lower()
        if singer and any(tok in qlow for tok in singer.split("、")):
            pick = x
            break
    pick = pick or lst[0]
    r2 = requests.get("http://krcs.kugou.com/search",
                      params={"ver": 1, "man": "yes", "client": "mobi", "keyword": "",
                              "duration": (pick.get("Duration") or 0) * 1000, "hash": pick["FileHash"]},
                      headers=UA, timeout=15)
    cands = r2.json().get("candidates") or []
    if not cands:
        return None
    c = cands[0]
    r3 = requests.get("http://lyrics.kugou.com/download",
                      params={"ver": 1, "client": "pc", "id": c["id"], "accesskey": c["accesskey"],
                              "fmt": "lrc", "charset": "utf8"},
                      headers=UA, timeout=15)
    d3 = r3.json()
    if not d3.get("content"):
        return None
    lrc = base64.b64decode(d3["content"]).decode("utf-8", errors="replace")
    return {"source": "kugou", "artist": pick.get("SingerName"), "title": pick.get("SongName"),
            "hash": pick["FileHash"], "synced": True, "lyrics": lrc}


# ---------------------------------------------------------------- 4. GENIUS via DUMB
DUMB_BASE = "https://dumb.ducks.party"  # instancia viva 2026-07; ver /instances.json para mas

def genius_dumb(query):
    """Letra plana de Genius via el frontend 'dumb' (github.com/rramiachraf/dumb)."""
    r = requests.get(f"{DUMB_BASE}/search", params={"q": query}, headers=UA, timeout=15)
    m = re.search(r"<h2>Songs</h2>(.*?)<div id=\"search-section\">", r.text, re.S)
    block = m.group(1) if m else r.text
    hits = re.findall(r'href="(/[^"]*-lyrics)"[^>]*>.*?<span>(.*?)</span><h3>(.*?)</h3>', block, re.S)
    pick = None
    for path, artist, title in hits:
        if "translation" not in path.lower() and "traduzione" not in path.lower():
            pick = (path, H.unescape(artist), H.unescape(title))
            break
    if not pick:
        return None
    r2 = requests.get(f"{DUMB_BASE}{pick[0]}", headers=UA, timeout=20)
    m2 = re.search(r'<div id="lyrics"[^>]*>(.*?)</div>', r2.text, re.S)
    if not m2:
        return None
    t = re.sub(r"<br\s*/?>", "\n", m2.group(1))
    t = re.sub(r"<[^>]+>", "", t)
    return {"source": "genius(dumb)", "artist": pick[1], "title": pick[2],
            "url": f"https://genius.com{pick[0]}", "synced": False,
            "lyrics": H.unescape(t).strip()}


# ---------------------------------------------------------------- 5. MEGALOBIZ
def _mega_get(url, params=None, tries=3):
    for i in range(tries):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=15)
            if r.status_code == 200:
                return r
        except requests.RequestException:
            pass
        time.sleep(1.5)
    return None

def megalobiz(query):
    """LRC sincronizado por scraping. Servidor flaky (500/503): reintentar.
    La busqueda es difusa: validar que el titulo del hit comparta palabras con la query."""
    r = _mega_get("https://www.megalobiz.com/search/all", {"qry": query})
    if not r:
        return None
    links = list(dict.fromkeys(re.findall(r'href="(/lrc/maker/[^"]+)"', r.text)))
    qwords = {w.lower() for w in query.split() if w.lower() not in STOPWORDS}
    pick = None
    for ln in links:
        name = re.sub(r"\.\d+$", "", ln.rsplit("/", 1)[-1]).replace("+", " ").lower()
        name = requests.utils.unquote(name)
        overlap = len(qwords & set(re.findall(r"\w+", name)))
        if overlap >= max(2, len(qwords) - 2):
            pick = ln
            break
    if not pick:
        return None
    r2 = _mega_get("https://www.megalobiz.com" + pick)
    if not r2:
        return None
    m = re.search(r'<div[^>]*class="lyrics_details[^"]*"[^>]*>\s*<span[^>]*>(.*?)</span>', r2.text, re.S)
    if not m:
        return None
    t = re.sub(r"<br\s*/?>", "\n", m.group(1))
    t = re.sub(r"<[^>]+>", "", t)
    lrc = re.sub(r"\n{2,}", "\n", H.unescape(t).strip())
    return {"source": "megalobiz", "url": "https://www.megalobiz.com" + pick,
            "synced": True, "lyrics": lrc}


# ---------------------------------------------------------------- demo
if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    queries = ["Bad Bunny Monaco", "Green A El Padre de la mentira"]
    fuentes = [letras_com, qq_music, kugou, genius_dumb, megalobiz]
    for q in queries:
        print("#" * 70)
        print("QUERY:", q)
        for fn in fuentes:
            try:
                res = fn(q)
            except Exception as e:
                print(f"  {fn.__name__:14s} ERROR {type(e).__name__}: {e}")
                continue
            if not res:
                print(f"  {fn.__name__:14s} sin resultado")
                continue
            body = [l for l in res["lyrics"].splitlines() if l.strip()]
            tag = "LRC " if res["synced"] else "TXT "
            print(f"  {fn.__name__:14s} OK {tag} {res.get('artist','?')} - {res.get('title','?')} | {len(body)} lineas")
            print("      ej:", body[6] if len(body) > 6 else body[-1])
