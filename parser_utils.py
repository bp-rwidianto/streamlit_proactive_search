import re
import pycountry
from collections import Counter
from rapidfuzz import fuzz
from affiliation import evaluate_affiliation_v2

STATE_TO_COUNTRY = {
    s: "US" for s in [
        "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN",
        "IA","KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV",
        "NH","NJ","NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN",
        "TX","UT","VT","VA","WA","WV","WI","WY",
    ]
}

INSTITUTION_KEYWORDS = [
    "university", "hospital", "medical center", "institute", "school",
    "clinic", "laboratory", "college", "health", "pharmaceuticals",
    "pharmaceutical", "center", "centre",
]


def load_models():
    import spacy
    from gliner import GLiNER
    try:
        nlp = spacy.load("en_core_web_sm")
    except OSError:
        from spacy.cli import download
        download("en_core_web_sm")
        nlp = spacy.load("en_core_web_sm")
    gliner_model = GLiNER.from_pretrained("urchade/gliner_medium-v2.1")
    return nlp, gliner_model


def extract_institution(affiliation: str) -> str:
    chunks = [x.strip() for x in affiliation.split(",")]
    parts = [c for c in chunks if any(kw in c.lower() for kw in INSTITUTION_KEYWORDS)]
    return ", ".join(parts)


def extract_geo(affiliation: str, nlp, gliner_model) -> dict:
    geo = {"city": None, "state": None, "country": None}

    entities = gliner_model.predict_entities(affiliation, ["city", "state", "country"])
    for ent in entities:
        label = ent["label"].lower()
        if label in geo and geo[label] is None:
            geo[label] = ent["text"]

    if not geo["city"] or not geo["country"]:
        doc = nlp(affiliation)
        gpes = [e.text for e in doc.ents if e.label_ in ("GPE", "LOC")]
        for item in gpes:
            item = item.strip()
            if item in STATE_TO_COUNTRY:
                geo["state"] = item
                geo["country"] = "US"
            elif not geo["city"]:
                geo["city"] = item

    if geo.get("state") in STATE_TO_COUNTRY:
        geo["country"] = STATE_TO_COUNTRY[geo["state"]]

    if geo["country"]:
        try:
            geo["country"] = pycountry.countries.lookup(geo["country"]).alpha_2
        except Exception:
            pass

    return geo


def parse_affiliation(affiliation: str, nlp, gliner_model) -> dict:
    institution = extract_institution(affiliation)
    geo = extract_geo(affiliation, nlp, gliner_model)
    return {
        "raw_affiliation": affiliation,
        "institution": institution,
        "city": geo["city"],
        "state": geo["state"],
        "country": geo["country"],
    }


def is_author_match(query_author: str, full_name: str, threshold: int = 85) -> bool:
    query_clean = query_author.replace("[Author]", "").lower().strip()
    full_clean  = full_name.lower().strip()
    if query_clean in full_clean:
        return True
    return fuzz.ratio(query_clean, full_clean) >= threshold


def extract_publication_summary(pub: dict, query_author: str, nlp, gliner_model) -> dict:
    pmid  = pub.get("pmid")
    title = pub.get("article_title") or ""
    date  = pub.get("date") or pub.get("pub_date") or pub.get("date_revised")

    matched_affiliations = []
    authors = pub.get("authors") or []
    if not isinstance(authors, list):
        authors = [authors]

    for author in authors:
        if not isinstance(author, dict):
            continue
        full_name = f"{author.get('ForeName', '')} {author.get('LastName', '')}".strip()
        if not is_author_match(query_author, full_name):
            continue

        affiliation = None
        aff_info = author.get("AffiliationInfo")
        if isinstance(aff_info, dict):
            affiliation = aff_info.get("Affiliation")
        elif isinstance(aff_info, list):
            affiliation = "; ".join(
                x.get("Affiliation", "") for x in aff_info
                if isinstance(x, dict) and x.get("Affiliation")
            )
        matched_affiliations.append({"author": full_name, "affiliation": affiliation})

    mesh_terms = [t for t in (pub.get("mesh") or []) if isinstance(t, str)]

    keywords = []
    for item in (pub.get("keywords") or []):
        if isinstance(item, str):
            keywords.append(item)
        elif isinstance(item, dict):
            kw = item.get("Keyword") or item.get("#text")
            if kw:
                keywords.append(kw)

    ror_id = None
    if matched_affiliations and matched_affiliations[0].get("affiliation"):
        try:
            parsed = parse_affiliation(matched_affiliations[0]["affiliation"], nlp, gliner_model)
            if parsed["institution"]:
                result = evaluate_affiliation_v2({
                    "query":   parsed["institution"],
                    "country": parsed["country"],
                })
                ror_id = result["ror"]["result"].get("ror_id")
        except Exception:
            pass

    return {
        "pmid":                 pmid,
        "title":                title,
        "date":                 date,
        "matched_affiliations": matched_affiliations,
        "mesh_terms":           mesh_terms,
        "keywords":             keywords,
        "matched_ror_id":       ror_id,
    }


def summarize_publications(publications: list) -> dict:
    unique_pmids = {p.get("pmid") for p in publications if p.get("pmid")}
    mesh_counter    = Counter()
    keyword_counter = Counter()

    for pub in publications:
        for term in (pub.get("mesh_terms") or []):
            if isinstance(term, str):
                mesh_counter[term] += 1
        for kw in (pub.get("keywords") or []):
            if isinstance(kw, str):
                keyword_counter[kw] += 1

    return {
        "number_of_publications": len(unique_pmids),
        "mesh":    dict(mesh_counter),
        "keyword": dict(keyword_counter),
    }
