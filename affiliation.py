import requests
import re
import country_converter as coco
from rapidfuzz import fuzz
from urllib.parse import quote

GENERIC_EMAIL_DOMAINS = {
    "gmail.com", "hotmail.com", "outlook.com",
    "icloud.com", "protonmail.com", "yahoo.com",
}

HIPOLABS_URL = "http://universities.hipolabs.com/search"


# ── Helpers ───────────────────────────────────────────────────────────────────

def extract_email_domain(email):
    if not email or "@" not in email:
        return None
    return email.split("@")[-1].lower().strip()

def country_to_alpha2(name):
    return coco.convert(names=name, to="ISO2")

def alpha2_to_name(code):
    return coco.convert(names=code, to="name_short")

def _to_alpha2(country_str):
    if not country_str:
        return None
    if re.fullmatch(r"[A-Z]{2}", country_str):
        return country_str
    converted = coco.convert(names=country_str, to="ISO2")
    return converted if converted != "not found" else None


# ── ROR ───────────────────────────────────────────────────────────────────────

def query_ror(query, country=None, email=None, timeout=30):
    query_ = query.replace("/", " ")
    domain = extract_email_domain(email)
    domain = domain if domain and domain not in GENERIC_EMAIL_DOMAINS else None
    country_filter = f"&filter=locations.geonames_details.country_code:{country}" if country else ""

    if domain:
        url = (
            f"https://api.ror.org/v2/organizations"
            f"?query.advanced=domains:{domain}+OR+links.value:{domain}{country_filter}"
        )
    else:
        url = f"https://api.ror.org/v2/organizations?query={query_}{country_filter}"

    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    return response.json().get("items", [])


def _score_name_trace(query, name_obj):
    name = (name_obj.get("value") or "").strip()
    types = name_obj.get("types", [])
    q = query.lower().strip()
    n = name.lower().strip()

    if not name:
        return -999, {"reason": "empty_name"}

    fuzzy = fuzz.token_set_ratio(q, n)
    type_weight = 0
    if "ror_display" in types: type_weight += 10
    if "label"       in types: type_weight += 7
    if "alias"       in types: type_weight += 3
    if "acronym"     in types: type_weight -= 8

    overlap_score  = len(set(q.split()) & set(n.split())) * 6
    exact_bonus    = 25 if q == n else 0
    substring_bonus = 15 if q in n else 0
    total = fuzzy + type_weight + overlap_score + exact_bonus + substring_bonus

    return total, {
        "name": name, "fuzzy": fuzzy, "type_weight": type_weight,
        "overlap_score": overlap_score, "exact_bonus": exact_bonus,
        "substring_bonus": substring_bonus, "total": total, "types": types,
    }


def select_best_ror_match_debug(items, query, email=None):
    debug_report = []
    email_domain = extract_email_domain(email)
    use_email_filter = bool(email_domain and email_domain not in GENERIC_EMAIL_DOMAINS)

    if use_email_filter:
        email_matches = [
            org for org in items
            if email_domain in [d.lower().strip().replace("www.", "") for d in org.get("domains", [])]
        ]
        items_to_score = email_matches if email_matches else items
    else:
        items_to_score = items

    best = None
    best_score = -1

    for org in items_to_score:
        org_best_score = -1
        org_best_trace = None
        org_best_name = None
        for name_obj in org.get("names", []):
            score, trace = _score_name_trace(query, name_obj)
            if score > org_best_score:
                org_best_score = score
                org_best_trace = trace
                org_best_name = name_obj
        debug_report.append({
            "org_id": org.get("id"),
            "best_score": org_best_score,
            "best_name": org_best_name,
            "trace": org_best_trace,
            "email_filtered": use_email_filter,
        })
        if org_best_score > best_score:
            best_score = org_best_score
            best = org

    return best, debug_report


def extract_ror_core_info(org):
    if not isinstance(org, dict):
        return {"ror_id": None, "city": None, "ror_names": None}

    ror_id = org.get("id")
    city = None
    location = org.get("location")
    if isinstance(location, dict):
        city = location.get("name")
    if not city:
        locations = org.get("locations")
        if isinstance(locations, list) and locations:
            city = locations[0].get("geonames_details", {}).get("name")

    ror_names = None
    names = org.get("names", [])
    if isinstance(names, list):
        for n in names:
            if "ror_display" in (n.get("types") or []):
                ror_names = n.get("value")
                break
        if not ror_names:
            for n in names:
                if n.get("lang") == "en" and "label" in (n.get("types") or []):
                    ror_names = n.get("value")
                    break
        if not ror_names:
            for n in names:
                if n.get("lang") == "en":
                    ror_names = n.get("value")
                    break
        if not ror_names and names:
            ror_names = names[0].get("value")

    return {"ror_id": ror_id, "city": city, "ror_names": ror_names}


# ── Hipolabs ──────────────────────────────────────────────────────────────────

def query_hipolabs(query=None, country=None, email=None, timeout=30):
    if email:
        domain = extract_email_domain(email)
        url = f"{HIPOLABS_URL}?domain={quote(domain)}"
    else:
        hipo_country = alpha2_to_name(country) if country else None
        url = f"{HIPOLABS_URL}?name={quote(query or '')}"
        if hipo_country:
            url += f"&country={quote(hipo_country)}"

    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    return [_normalize_hipolabs_record(x) for x in response.json()]


def _normalize_hipolabs_record(university):
    return {
        "id": university.get("web_pages", [None])[0],
        "names": [{"value": university.get("name")}],
        "location": {"name": university.get("country")},
        "domains": university.get("domains", []),
        "source": "hipolabs",
    }


def select_best_match(items, query, email=None):
    if not items:
        return {}, []
    best_score = -1
    best_item = None
    email_domain = extract_email_domain(email) if email else None
    debug = []

    for item in items:
        names = item.get("names", [])
        if not names:
            continue
        name = names[0].get("value", "")
        score = fuzz.token_sort_ratio(query.lower(), name.lower())
        if email_domain and email_domain in item.get("domains", []):
            score += 50
        debug.append({"name": name, "score": score})
        if score > best_score:
            best_score = score
            best_item = item

    return best_item, debug


def extract_core_info(org):
    if not org:
        return {}
    return {
        "id": org.get("id"),
        "name": org.get("names", [{}])[0].get("value"),
        "city": org.get("location", {}).get("name"),
        "source": org.get("source", "ror"),
    }


# ── Hipolabs with subdomain-strip retry ──────────────────────────────────────

def _strip_one_subdomain(domain: str):
    parts = domain.split(".")
    if len(parts) <= 2:
        return None
    return ".".join(parts[1:])


def _query_hipolabs_with_retry(query, country, email):
    domain = extract_email_domain(email) if email else None
    use_email = domain and domain not in GENERIC_EMAIL_DOMAINS

    if use_email:
        items = query_hipolabs(query=query, country=country, email=email)
        if items:
            best, debug = select_best_match(items, query or "", email)
            return items, best, debug
        shorter = _strip_one_subdomain(domain)
        if shorter:
            fake_email = f"x@{shorter}"
            items = query_hipolabs(query=query, country=country, email=fake_email)
            if items:
                best, debug = select_best_match(items, query or "", fake_email)
                return items, best, debug

    items = query_hipolabs(query=query, country=country, email=None)
    best, debug = select_best_match(items, query or "", None)
    return items, best, debug


# ── Public evaluate functions ─────────────────────────────────────────────────

def evaluate_affiliation(query, country=None, email=None):
    if country and not re.fullmatch(r"[A-Z]{2}", country):
        converted = country_to_alpha2(country)
        if converted != "not found":
            country = converted

    items = query_ror(query, country)
    ror_best, ror_debug = select_best_ror_match_debug(items, query, email)
    hipolabs_items = query_hipolabs(query=query, country=country, email=email)
    hipo_best, hipo_debug = select_best_match(hipolabs_items, query or "", email)

    return {
        "ror":      {"result": extract_ror_core_info(ror_best), "debug": ror_debug},
        "hipolabs": {"result": extract_core_info(hipo_best),    "debug": hipo_debug},
    }


def evaluate_affiliation_v2(aff_data: dict) -> dict:
    email   = aff_data.get("email")
    country = _to_alpha2(aff_data.get("country"))
    query   = aff_data.get("query")

    if not query and not email:
        return {"ror": {"result": {}, "debug": []}, "hipolabs": {"result": {}, "debug": []}}

    try:
        ror_items = query_ror(query or "", country, email)
        ror_best, ror_debug = select_best_ror_match_debug(ror_items, query or "", email)
        ror_result = extract_ror_core_info(ror_best)
    except Exception as e:
        ror_result, ror_debug = {}, [{"error": str(e)}]

    try:
        _, hipo_best, hipo_debug = _query_hipolabs_with_retry(query, country, email)
        hipo_result = extract_core_info(hipo_best)
    except Exception as e:
        hipo_result, hipo_debug = {}, [{"error": str(e)}]

    return {
        "ror":      {"result": ror_result,  "debug": ror_debug},
        "hipolabs": {"result": hipo_result, "debug": hipo_debug},
    }
