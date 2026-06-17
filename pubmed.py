import urllib.request
import json
import xmltodict
import re
import math
import time
import statistics
import urllib.parse
from datetime import datetime
import os

NCBI_ESEARCH_API_KEY = os.getenv("NCBI_ESEARCH_API_KEY", "3cdaf027ec792997ff53b415e26105145808")
NCBI_EFETCH_API_KEY  = os.getenv("NCBI_EFETCH_API_KEY",  "1f136faf3ee5800d6b247f7ad67fb1d62a09")


# ── Low-level HTTP helpers ────────────────────────────────────────────────────

def _fetch(url: str) -> bytes:
    with urllib.request.urlopen(url) as r:
        return r.read()

def _fetch_xml(url: str) -> dict:
    return xmltodict.parse(_fetch(url))


# ── Date helpers ──────────────────────────────────────────────────────────────

_MONTH_MAP = {
    "Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04",
    "May": "05", "Jun": "06", "Jul": "07", "Aug": "08",
    "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12",
}

def _convert_month(value: str) -> str:
    try:
        abbr = value.split("-")[1]
    except Exception:
        abbr = "01"
    return _MONTH_MAP.get(abbr, "01")

def _handle_dates(dates: list) -> list:
    result = []
    for d in dates:
        if d:
            try:
                year  = re.search(r"[0-9]{4}", d).group()
                month = _convert_month(d)
                result.append(f"{year}-{month}")
            except Exception:
                result.append("2025-12")
    return sorted(result) if result else ["2025-12"]


# ── XML response handlers ─────────────────────────────────────────────────────

def _handle_mesh(mesh_list) -> list:
    result = []
    if not isinstance(mesh_list, list):
        mesh_list = [mesh_list]
    for mesh in mesh_list:
        try: result.append(mesh["DescriptorName"]["#text"])
        except Exception: pass
        try: result.append(mesh["QualifierName"]["#text"])
        except Exception: pass
    return result

def _handle_keywords(kw_list) -> list:
    if not isinstance(kw_list, list):
        kw_list = [kw_list]
    result = []
    for kw in kw_list:
        if isinstance(kw, str):
            result.append(kw)
        elif isinstance(kw, dict):
            text = kw.get("#text") or kw.get("Keyword") or ""
            if text:
                result.append(text)
    return result

def _normalize_title(title) -> str:
    if isinstance(title, dict):
        return title.get("#text", str(title))
    return title or ""

def _handle_dict_metadata(record: dict) -> dict:
    mc = record.get("MedlineCitation", {})
    art = mc.get("Article", {})

    try:    pmid = mc["PMID"]["#text"]
    except Exception: pmid = None

    try:    date_completed = "{Year}-{Month}-{Day}".format(**mc["DateCompleted"])
    except Exception: date_completed = None

    try:    date_revised = "{Year}-{Month}-{Day}".format(**mc["DateRevised"])
    except Exception: date_revised = None

    try:
        ad = art["ArticleDate"]
        pub_date = f"{ad['Year']}-{ad.get('Month', '01')}"
    except Exception:
        pub_date = None

    try:    article_title = _normalize_title(art["ArticleTitle"])
    except Exception: article_title = None

    try:    pub_title = art["Journal"]["Title"]
    except Exception: pub_title = None

    try:    abstract = art["Abstract"]["AbstractText"]
    except Exception: abstract = None
    if isinstance(abstract, dict):
        abstract = abstract.get("#text", "")

    try:
        authors = art["AuthorList"]["Author"]
        if not isinstance(authors, list):
            authors = [authors]
    except Exception:
        authors = []

    try:    pub_medium = art["Journal"]["JournalIssue"]["@CitedMedium"]
    except Exception: pub_medium = None

    try:    country = mc["MedlineJournalInfo"]["Country"]
    except Exception: country = None

    try:    mesh = _handle_mesh(mc["MeshHeadingList"]["MeshHeading"])
    except Exception: mesh = []

    try:    keywords = _handle_keywords(mc["KeywordList"]["Keyword"])
    except Exception: keywords = []

    true_date = _handle_dates([pub_date, date_completed, date_revised])[0]

    return {
        "pmid":           pmid,
        "date":           true_date,
        "date_completed": date_completed,
        "date_revised":   date_revised,
        "pub_medium":     pub_medium,
        "pub_date":       pub_date,
        "pub_title":      pub_title,
        "article_title":  article_title,
        "abstract":       abstract,
        "authors":        authors,
        "country":        country,
        "mesh":           mesh,
        "keywords":       keywords,
    }


# ── E-utilities fetch functions ───────────────────────────────────────────────

def fetch_document_ids(term: str, date_start: str = "0") -> dict:
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    date_part = f"&mindate=1900/01&maxdate={date_start}" if date_start != "0" else ""
    url = (
        f"{base}?db=pubmed&term={term}{date_part}"
        f"&retmax=10000&sort=pub_date&retmode=json"
        f"&api_key={NCBI_ESEARCH_API_KEY}"
    )
    response = json.loads(_fetch(url))
    list_id = response["esearchresult"]["idlist"]
    return {"list_id": list_id, "url": url}


def fetch_document_metadata(ids: list) -> dict:
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
    url = (
        f"{base}?db=pubmed&id={','.join(ids)}"
        f"&retmax=10000&api_key={NCBI_EFETCH_API_KEY}"
    )
    response = _fetch_xml(url)
    articles = response.get("PubmedArticleSet", {}).get("PubmedArticle", [])
    if not isinstance(articles, list):
        articles = [articles]

    documents = {}
    for article in articles:
        try:
            parsed = _handle_dict_metadata(article)
            if parsed["pmid"]:
                documents[parsed["pmid"]] = parsed
        except Exception:
            pass
    return documents


def bulk_fetch_document_metadata(ids: list) -> dict:
    if not ids:
        return {}
    if len(ids) <= 100:
        return fetch_document_metadata(ids)
    result = {}
    for i in range(0, math.ceil(len(ids) / 100)):
        batch = ids[i * 100: (i + 1) * 100]
        if not batch:
            break
        result.update(fetch_document_metadata(batch))
        time.sleep(0.5)
    return result


def get_earliest_document_date(metadata: dict, sample: int = 100) -> str | None:
    pub_dates = []
    for key in list(metadata.keys())[-sample:]:
        rec = metadata[key]
        true_date = _handle_dates([
            rec.get("pub_date"),
            rec.get("date_completed"),
            rec.get("date_revised"),
        ])[0]
        pub_dates.append(true_date)
    if not pub_dates:
        return None
    try:
        mode_date = statistics.mode(pub_dates)
        return datetime.strptime(mode_date, "%Y-%m").strftime("%Y %b")
    except Exception:
        return None


# ── Main publication fetcher ──────────────────────────────────────────────────

def get_publications(terms: list, log_callback=None) -> dict:
    if log_callback is None:
        log_callback = print

    today = datetime.today()
    target_date = datetime.strptime("2015 Jan", "%Y %b")
    bulk_metadata: dict = {}
    seen_ids: set = set()

    log_callback(
        f"Fetching publications from **{today.strftime('%Y %b')}** back to **{target_date.strftime('%Y %b')}**"
    )

    for idx, query in enumerate(terms, start=1):
        current_date = today
        retry_count  = 0
        max_retry    = 5

        log_callback(f"Query {idx}/{len(terms)}: `{query}`")

        while current_date >= target_date:
            try:
                time.sleep(0.5)
                date_param = current_date.strftime("%Y/%m")
                log_callback(f"→ Fetching up to: **{current_date.strftime('%Y %b')}**")

                response     = fetch_document_ids(urllib.parse.quote(query), date_start=date_param)
                id_list      = response.get("list_id", [])
                new_ids      = [i for i in id_list if i not in seen_ids]

                if not new_ids:
                    log_callback("No new IDs — done.")
                    break

                metadata = bulk_fetch_document_metadata(new_ids)
                if not metadata:
                    log_callback("No metadata returned — done.")
                    break

                for k, v in metadata.items():
                    if k not in bulk_metadata:
                        bulk_metadata[k] = v
                seen_ids.update(new_ids)

                log_callback(
                    f"✅ +{len(metadata)} publications (running total: **{len(bulk_metadata)}**)"
                )

                next_date_str = get_earliest_document_date(metadata)
                if not next_date_str:
                    log_callback("Invalid next date — done.")
                    break

                try:
                    next_date = datetime.strptime(next_date_str, "%Y %b")
                except Exception:
                    log_callback(f"Cannot parse date `{next_date_str}` — done.")
                    break

                if next_date >= current_date:
                    log_callback("Date did not progress — done.")
                    break

                current_date = next_date
                retry_count  = 0

            except Exception as e:
                retry_count += 1
                log_callback(f"⚠️ Error: {e} (retry {retry_count}/{max_retry})")
                if retry_count >= max_retry:
                    log_callback("Max retries exceeded — skipping query.")
                    break
                log_callback("Waiting 60 s before retry...")
                time.sleep(60)

    return bulk_metadata
