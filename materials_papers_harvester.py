#!/usr/bin/env python3
"""
materials_papers_harvester.py  — NO PDF DOWNLOADING

Search materials-science literature by topic keywords across multiple sources,
enrich missing PDF links via Unpaywall + landing-page scraping, score, dedupe,
and export JSONL/CSV. (All PDF *downloading* code and flags are removed.)

This version fixes:
- DOAJ: use v4 endpoint with path-embedded query and page_size
- OpenAlex: first page without cursor, sort by publication_date, use real mailto

Usage example:
  python materials_papers_harvester.py \
    --query "perovskite thin films defect passivation" \
    --from-year 2015 --to-year 2025 \
    --max-per-source 200 \
    --strict \
    --out runs/materials_harvest/results.jsonl \
    --csv runs/materials_harvest/results.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urljoin, urlparse, quote

import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

# Optional deps
try:
    from rapidfuzz import fuzz, process as rf_process
except Exception:
    fuzz = None
    rf_process = None

try:
    from bs4 import BeautifulSoup
except Exception:
    BeautifulSoup = None  # scraping will fallback to regex if BS4 missing

# --------------------------- Identity & headers ---------------------------

# Static as requested
UNPAYWALL_EMAIL = "mohamed.aneddame-ext@um6p.ma"
# Use a real mailto for OpenAlex; fallback to the same static email
CROSSREF_EMAIL = os.getenv("CROSSREF_EMAIL") or os.getenv("EMAIL") or UNPAYWALL_EMAIL

USER_AGENT = (
    "materials-papers-harvester/1.3 (+https://example.org; contact: "
    f"{CROSSREF_EMAIL})"
)

HEADERS_JSON = {"User-Agent": USER_AGENT, "Accept": "application/json"}
HEADERS_TEXT = {"User-Agent": USER_AGENT, "Accept": "*/*"}

SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en;q=0.9",
    }
)
TIMEOUT = 15


# --------------------------- HTTP helpers ---------------------------

class HttpError(Exception):
    pass


@retry(
    reraise=True,
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=0.8, min=1, max=10),
    retry=retry_if_exception_type((requests.RequestException, HttpError)),
)
def _get_json(
    url: str, params: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, str]] = None
) -> Any:
    h = dict(HEADERS_JSON)
    if headers:
        h.update(headers)
    r = SESSION.get(url, params=params, headers=h, timeout=TIMEOUT)
    if r.status_code == 429:
        raise HttpError("429 Too Many Requests")
    r.raise_for_status()
    return r.json()


@retry(
    reraise=True,
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=0.8, min=1, max=10),
    retry=retry_if_exception_type((requests.RequestException, HttpError)),
)
def _get_text(
    url: str, params: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, str]] = None
) -> str:
    h = dict(HEADERS_TEXT)
    if headers:
        h.update(headers)
    r = SESSION.get(url, params=params, headers=h, timeout=TIMEOUT)
    if r.status_code == 429:
        raise HttpError("429 Too Many Requests")
    r.raise_for_status()
    return r.text


# --------------------------- Utilities ---------------------------

MATERIALS_KEYWORDS = [
    r"materials? science",
    r"functional materials",
    r"semiconductor(s)?",
    r"battery|cathode|anode|electrolyte|solid-state|intercalation",
    r"perovskite(s)?|spinel|garnet|oxide|sulfide|nitride|carbide|boride",
    r"alloy(s)?|steel|superalloy|HEA|high-entropy",
    r"polymer(s)?|composite(s)?",
    r"thin film(s)?|coating(s)?|deposition|ALD|CVD|PVD|sputter",
    r"microstructure|grain|phase|diffusion|defect(s)?|dislocation",
    r"catalyst(s)?|electrocatalysis|photocatalysis",
]
MATERIALS_RE = re.compile("(" + "|".join(MATERIALS_KEYWORDS) + ")", re.I)
EXCLUDE_RE = re.compile(r"\b(nursing|clinical|veterinary|pediat|oncolog|dermatolog|surgery)\b", re.I)


def norm(s: Optional[str]) -> str:
    return (s or "").strip().replace("\u00a0", " ")


def safe_year(s: Optional[str]) -> Optional[int]:
    if not s:
        return None
    try:
        m = re.search(r"(19|20)\d{2}", s)
        return int(m.group(0)) if m else None
    except Exception:
        return None


# --------------------------- Data model ---------------------------

@dataclass
class Record:
    title: str = ""
    abstract: str = ""
    year: Optional[int] = None
    doi: Optional[str] = None
    url: Optional[str] = None
    pdf_url: Optional[str] = None
    venue: Optional[str] = None
    authors: List[str] = field(default_factory=list)
    source: Optional[str] = None
    score: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "abstract": self.abstract,
            "year": self.year,
            "doi": self.doi,
            "url": self.url,
            "pdf_url": self.pdf_url,
            "venue": self.venue,
            "authors": self.authors,
            "source": self.source,
            "_score": self.score,
        }


# --------------------------- Scoring ---------------------------

def score_record(rec: Record, query: str) -> float:
    s = 0.0
    q = query.lower()
    hay = f"{rec.title}\n{rec.abstract}".lower()
    for token in set(re.findall(r"[a-z0-9\-]{3,}", q)):
        if token in hay:
            s += 1.0
    if MATERIALS_RE.search(hay) or MATERIALS_RE.search((rec.venue or "")):
        s += 2.5
    if EXCLUDE_RE.search(hay):
        s -= 3.0
    if any(t in rec.title.lower() for t in set(q.split())):
        s += 1.0
    if rec.pdf_url:
        s += 1.0
    if rec.year:
        s += max(0.0, min(2.0, (rec.year - 2000) / 12.0))
    return s


# --------------------------- Normalization helper ---------------------------

def make_record(
    *,
    title: str,
    abstract: str = "",
    year: Optional[int],
    doi: Optional[str],
    url: Optional[str],
    pdf_url: Optional[str],
    venue: Optional[str],
    authors: Iterable[str],
    source: str,
) -> Record:
    return Record(
        title=norm(title),
        abstract=norm(abstract),
        year=year,
        doi=norm(doi) or None,
        url=norm(url) or None,
        pdf_url=norm(pdf_url) or None,
        venue=norm(venue) or None,
        authors=[norm(a) for a in authors if norm(a)],
        source=source,
    )


# --------------------------- Sources ---------------------------

def search_openalex(query: str, y0: int, y1: int, limit: int) -> List[Record]:
    """
    Fixed:
    - First request without cursor
    - Stable sort by publication_date
    - mailto uses a real email
    """
    out: List[Record] = []
    base = "https://api.openalex.org/works"
    per_page = min(200, max(25, limit))
    cursor: Optional[str] = None  # first call without cursor
    while len(out) < limit:
        params = {
            "search": query,
            "filter": f"from_publication_date:{y0}-01-01,to_publication_date:{y1}-12-31,"
                      f"type:journal-article|proceedings-article",
            "per-page": per_page,
            "sort": "publication_date:desc",
            "mailto": UNPAYWALL_EMAIL,  # real email
        }
        if cursor:
            params["cursor"] = cursor
        data = _get_json(base, params=params)
        for w in data.get("results", []):
            title = w.get("title") or ""
            doi = (w.get("doi") or "").replace("https://doi.org/", "") or None
            url = (
                w.get("primary_location", {}).get("source", {}).get("host_page_url")
                or w.get("primary_location", {}).get("landing_page_url")
            )
            pdf = w.get("primary_location", {}).get("pdf_url") or (w.get("open_access", {}) or {}).get("oa_url")
            year = safe_year(str(w.get("publication_year")))
            venue = (w.get("host_venue", {}) or {}).get("display_name")
            authors = [a.get("author", {}).get("display_name", "") for a in w.get("authorships", [])]
            out.append(
                make_record(
                    title=title,
                    abstract="",
                    year=year,
                    doi=doi,
                    url=url,
                    pdf_url=pdf,
                    venue=venue,
                    authors=authors,
                    source="OpenAlex",
                )
            )
            if len(out) >= limit:
                break
        cursor = (data.get("meta", {}) or {}).get("next_cursor")
        if not cursor:
            break
        time.sleep(random.uniform(0.1, 0.3))
    return out


def search_crossref(query: str, y0: int, y1: int, limit: int) -> List[Record]:
    out: List[Record] = []
    base = "https://api.crossref.org/works"
    rows = 100
    cursor = "*"
    while len(out) < limit:
        params = {
            "query": query,
            "filter": f"from-pub-date:{y0}-01-01,until-pub-date:{y1}-12-31,type:journal-article",
            "rows": rows,
            "cursor": cursor,
            "mailto": CROSSREF_EMAIL,
        }
        data = _get_json(base, params=params)
        items = data.get("message", {}).get("items", [])
        for it in items:
            title = (it.get("title") or [""])[0]
            doi = it.get("DOI")
            url = it.get("URL")
            year = safe_year(str(it.get("issued", {}).get("date-parts", [[None]])[0][0]))
            venue = it.get("container-title", [""])[0]
            authors = [" ".join(filter(None, [a.get("given"), a.get("family")])) for a in it.get("author", [])]
            pdf_url = None
            for lk in (it.get("link", []) or []):
                ct = (lk.get("content-type") or lk.get("content_type") or "").lower()
                if "pdf" in ct and lk.get("URL"):
                    pdf_url = lk["URL"]
                    break
            out.append(
                make_record(
                    title=title,
                    year=year,
                    doi=doi,
                    url=url,
                    pdf_url=pdf_url,
                    venue=venue,
                    authors=authors,
                    abstract="",
                    source="Crossref",
                )
            )
            if len(out) >= limit:
                break
        cursor = data.get("message", {}).get("next-cursor")
        if not cursor:
            break
        time.sleep(random.uniform(0.1, 0.3))
    return out


def search_arxiv(query: str, y0: int, y1: int, limit: int) -> List[Record]:
    out: List[Record] = []
    base = "http://export.arxiv.org/api/query"
    start = 0
    step = 100
    q = f"(all:{query.replace(' ', '+')}) AND (cat:cond-mat.mtrl-sci OR cat:physics.chem-ph)"
    while len(out) < limit:
        params = {
            "search_query": q,
            "start": start,
            "max_results": step,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
        text = _get_text(base, params=params, headers={"Accept": "application/atom+xml"})
        for entry in re.split(r"</entry>", text):
            t = re.search(r"<title>(.*?)</title>", entry, re.S)
            if not t:
                continue
            title = norm(re.sub(r"\s+", " ", t.group(1)))
            abs_m = re.search(r"<summary>(.*?)</summary>", entry, re.S)
            abstract = norm(re.sub(r"\s+", " ", abs_m.group(1))) if abs_m else ""
            year = int(re.search(r"<published>(\d{4})-", entry).group(1)) if re.search(r"<published>(\d{4})-", entry) else None
            if year and not (y0 <= year <= y1):
                continue
            url_m = re.search(r"<id>(.*?)</id>", entry)
            url = url_m.group(1) if url_m else None
            pdf_m = re.search(r'href="(https?://arxiv.org/pdf/[^"]+)"', entry)
            pdf_url = pdf_m.group(1) if pdf_m else None
            out.append(
                make_record(
                    title=title,
                    abstract=abstract,
                    year=year,
                    doi=None,
                    url=url,
                    pdf_url=pdf_url,
                    venue="arXiv",
                    authors=[],
                    source="arXiv",
                )
            )
            if len(out) >= limit:
                break
        if len(out) >= limit or ("</feed>" in text and "<entry>" not in text):
            break
        start += step
        time.sleep(random.uniform(0.1, 0.3))
    return out


def search_semantic_scholar(query: str, y0: int, y1: int, limit: int) -> List[Record]:
    """
    Fixed:
    - Use nested field 'authors.name' instead of invalid top-level 'name'
    - If the API still returns 400 (schema drift), retry once without 'fields'
    """
    out: List[Record] = []
    key = os.getenv("SEMANTIC_SCHOLAR_API_KEY")
    headers = {"x-api-key": key} if key else {}
    base = "https://api.semanticscholar.org/graph/v1/paper/search"
    offset = 0
    step = min(100, max(10, limit))

    def fetch_page(use_fields: bool = True):
        params = {
            "query": query,
            "year": f"{y0}-{y1}",            # range is supported
            "limit": step,
            "offset": offset,
        }
        if use_fields:
            # IMPORTANT: authors.name is the valid nested field (not 'name')
            params["fields"] = "title,abstract,year,externalIds,url,openAccessPdf,venue,authors.name"
        return _get_json(base, params=params, headers=headers)

    use_fields = True
    while len(out) < limit:
        try:
            data = fetch_page(use_fields=use_fields)
        except Exception as e:
            # If first try with fields caused a 400, retry once without fields
            if use_fields:
                try:
                    data = fetch_page(use_fields=False)
                    use_fields = False  # stick to no-fields for subsequent pages
                except Exception:
                    # Give up on S2 for this run
                    print(f"[warn] SemanticScholar failed: {e}")
                    break
            else:
                print(f"[warn] SemanticScholar failed: {e}")
                break

        items = data.get("data", [])
        if not items:
            break

        for p in items:
            if len(out) >= limit:
                break
            title = p.get("title") or ""
            abstract = p.get("abstract") or ""
            year = p.get("year")
            doi = (p.get("externalIds", {}) or {}).get("DOI")
            url = p.get("url")
            pdf = (p.get("openAccessPdf") or {}).get("url")
            venue = p.get("venue")
            # authors may be list[dict{name: str}] if fields were requested; otherwise may be absent
            authors = []
            for a in (p.get("authors") or []):
                name = a.get("name") if isinstance(a, dict) else None
                if name:
                    authors.append(name)

            out.append(
                make_record(
                    title=title,
                    abstract=abstract,
                    year=year,
                    doi=doi,
                    url=url,
                    pdf_url=pdf,
                    venue=venue,
                    authors=authors,
                    source="SemanticScholar",
                )
            )

        offset += step
        time.sleep(random.uniform(0.1, 0.3))

    if not key:
        print("[warn] Semantic Scholar key not set; results limited by public quota.")
    return out



def search_doaj(query: str, y0: int, y1: int, limit: int) -> List[Record]:
    """
    Fixed to DOAJ v4:
    - Endpoint: /api/search/articles/{URL-ENCODED-QUERY}
    - Params: page, page_size
    - Query includes date filter on created_date
    """
    out: List[Record] = []
    base = "https://doaj.org/api/search/articles/"
    page = 1
    step = min(100, max(10, limit))  # v4 page_size
    # build path-embedded query
    q = f"{query} AND created_date:[{y0}-01-01 TO {y1}-12-31]"
    q_path = base + quote(q, safe="")
    while len(out) < limit:
        params = {"page": page, "page_size": step}
        data = _get_json(q_path, params=params)
        results = data.get("results", [])
        for r in results:
            bib = r.get("bibjson", {})
            title = bib.get("title", "")
            abstract = bib.get("abstract", "")
            year = safe_year(str(bib.get("year")))
            doi = next((iden.get("id") for iden in bib.get("identifier", []) if iden.get("type") == "doi"), None)
            url = (bib.get("link", [{}])[0] or {}).get("url")
            pdf = None
            for lk in bib.get("link", []):
                if (lk.get("type") == "fulltext") and (lk.get("content_type", "") or "").lower() == "application/pdf":
                    pdf = lk.get("url")
            venue = (bib.get("journal", {}) or {}).get("title")
            authors = [a.get("name", "") for a in bib.get("author", [])]
            out.append(
                make_record(
                    title=title,
                    abstract=abstract,
                    year=year,
                    doi=doi,
                    url=url,
                    pdf_url=pdf,
                    venue=venue,
                    authors=authors,
                    source="DOAJ",
                )
            )
            if len(out) >= limit:
                break
        if not results:
            break
        page += 1
        time.sleep(random.uniform(0.1, 0.3))
    return out


def search_pubmed(query: str, y0: int, y1: int, limit: int) -> List[Record]:
    out: List[Record] = []
    email = CROSSREF_EMAIL
    tool = "materials-papers-harvester"
    esearch = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    params = {
        "db": "pubmed",
        "term": f"{query}",
        "retmax": min(10000, limit),
        "retmode": "json",
        "tool": tool,
        "email": email,
        "mindate": y0,
        "maxdate": y1,
    }
    data = _get_json(esearch, params=params)
    ids = (data.get("esearchresult", {}) or {}).get("idlist", [])
    if not ids:
        return out
    esummary = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
    params = {"db": "pubmed", "id": ",".join(ids), "retmode": "json"}
    summ = _get_json(esummary, params=params)
    res = summ.get("result", {}) or {}
    for pid in ids:
        it = res.get(pid) or {}
        title = it.get("title", "")
        abstract = ""
        year = safe_year(it.get("pubdate"))
        doi = None
        if it.get("articleids"):
            for iden in it["articleids"]:
                if iden.get("idtype") == "doi":
                    doi = iden.get("value")
        url = it.get("elocationid") or it.get("sortfirstauthor")
        venue = it.get("source")
        authors = [a.get("name", "") for a in it.get("authors", [])]
        out.append(
            make_record(
                title=title,
                abstract=abstract,
                year=year,
                doi=doi,
                url=url,
                pdf_url=None,
                venue=venue,
                authors=authors,
                source="PubMed",
            )
        )
        if len(out) >= limit:
            break
    return out


def search_springer(query: str, y0: int, y1: int, limit: int) -> List[Record]:
    key = os.getenv("SPRINGER_API_KEY")
    if not key:
        print("[warn] SPRINGER_API_KEY not set; skipping Springer.")
        return []
    out: List[Record] = []
    base = "https://api.springernature.com/metadata/json"
    page = 1
    step = 50
    while len(out) < limit:
        params = {"q": f"{query} AND year:{y0}-{y1}", "p": step, "s": (page - 1) * step + 1, "api_key": key}
        data = _get_json(base, params=params)
        for r in data.get("records", []):
            title = r.get("title", "")
            abstract = r.get("abstract", "")
            year = safe_year(r.get("publicationDate"))
            doi = next((iden[4:] for iden in r.get("identifier", []) if iden.startswith("doi:")), None)
            pdf_url = None
            html_url = None
            for u in (r.get("url", []) or []):
                fmt = str(u.get("format", "")).lower()
                if fmt == "pdf" and not pdf_url:
                    pdf_url = u.get("value")
                elif fmt == "html" and not html_url:
                    html_url = u.get("value")
            url = html_url or pdf_url
            venue = r.get("publicationName")
            authors = [a.get("creator", "") for a in r.get("creators", [])]
            out.append(
                make_record(
                    title=title,
                    abstract=abstract,
                    year=year,
                    doi=doi,
                    url=url,
                    pdf_url=pdf_url,
                    venue=venue,
                    authors=authors,
                    source="Springer",
                )
            )
            if len(out) >= limit:
                break
        if not data.get("records"):
            break
        page += 1
        time.sleep(random.uniform(0.1, 0.3))
    return out


def search_sciencedirect(query: str, y0: int, y1: int, limit: int) -> List[Record]:
    key = os.getenv("ELSEVIER_API_KEY")
    if not key:
        print("[warn] ELSEVIER_API_KEY not set; skipping ScienceDirect.")
        return []
    out: List[Record] = []
    base = "https://api.elsevier.com/content/search/sciencedirect"
    start = 0
    count = 25
    while len(out) < limit:
        params = {
            "query": f"TITLE-ABS-KEY({query})",
            "date": f"{y0}-{y1}",
            "count": count,
            "start": start,
            "apiKey": key,
        }
        data = _get_json(base, params=params)
        entries = (data.get("search-results", {}) or {}).get("entry", [])
        for e in entries:
            title = e.get("dc:title", "")
            url = e.get("link", [{}])[0].get("@href") if isinstance(e.get("link"), list) else None
            year = safe_year(e.get("prism:coverDate"))
            doi = e.get("prism:doi")
            venue = e.get("prism:publicationName")
            out.append(
                make_record(
                    title=title,
                    abstract="",
                    year=year,
                    doi=doi,
                    url=url,
                    pdf_url=None,
                    venue=venue,
                    authors=[],
                    source="ScienceDirect",
                )
            )
            if len(out) >= limit:
                break
        if not entries:
            break
        start += count
        time.sleep(random.uniform(0.1, 0.3))
    return out


def search_ieee(query: str, y0: int, y1: int, limit: int) -> List[Record]:
    key = os.getenv("IEEE_API_KEY")
    if not key:
        print("[warn] IEEE_API_KEY not set; skipping IEEE Xplore.")
        return []
    out: List[Record] = []
    base = "https://ieeexploreapi.ieee.org/api/v1/search/articles"
    start = 1
    rows = 200
    while len(out) < limit:
        params = {
            "apikey": key,
            "querytext": query,
            "start_record": start,
            "max_records": rows,
            "start_year": y0,
            "end_year": y1,
        }
        data = _get_json(base, params=params)
        arts = data.get("articles", [])
        for a in arts:
            title = a.get("title") or ""
            abstract = a.get("abstract") or ""
            year = safe_year(str(a.get("publication_year")))
            doi = a.get("doi")
            url = a.get("html_url") or a.get("pdf_url")
            pdf = a.get("pdf_url")
            venue = a.get("publication_title")
            authors = (
                [au.get("full_name", "") for au in a.get("authors", {}).get("authors", [])]
                if isinstance(a.get("authors"), dict)
                else []
            )
            out.append(
                make_record(
                    title=title,
                    abstract=abstract,
                    year=year,
                    doi=doi,
                    url=url,
                    pdf_url=pdf,
                    venue=venue,
                    authors=authors,
                    source="IEEE Xplore",
                )
            )
            if len(out) >= limit:
                break
        if not arts:
            break
        start += rows
        time.sleep(random.uniform(0.1, 0.3))
    return out


# --------------------------- Enrichment: Unpaywall ---------------------------

def enrich_unpaywall(recs: List[Record]) -> None:
    """Fill missing pdf_url/url from Unpaywall (when DOI present)."""
    for r in recs:
        if r.pdf_url or not r.doi:
            continue
        url = f"https://api.unpaywall.org/v2/{r.doi}"
        try:
            data = _get_json(url, params={"email": UNPAYWALL_EMAIL})
        except Exception:
            continue
        candidates = []
        best = (data.get("best_oa_location") or {})
        if best:
            candidates.append(best)
        candidates += (data.get("oa_locations") or [])
        for c in candidates:
            if not r.pdf_url and c.get("url_for_pdf"):
                r.pdf_url = c["url_for_pdf"]
            if not r.url and c.get("url"):
                r.url = c["url"]
            if r.pdf_url:
                break
        time.sleep(random.uniform(0.05, 0.15))


# --------------------------- Enrichment: landing-page scraping ---------------

def resolve_doi(doi: str) -> str:
    try:
        r = SESSION.get(f"https://doi.org/{doi}", allow_redirects=True, timeout=25)
        r.raise_for_status()
        return r.url
    except Exception:
        return ""


def validate_pdf(url: str, validate=True) -> bool:
    if not validate:
        return True
    try:
        r = SESSION.head(url, allow_redirects=True, timeout=20)
        ctype = (r.headers.get("Content-Type") or "").lower()
        if "application/pdf" in ctype:
            return True
    except Exception:
        pass
    try:
        r = SESSION.get(url, stream=True, timeout=25)
        r.raise_for_status()
        chunk = next(r.iter_content(2048), b"")
        if chunk[:5] == b"%PDF-":
            return True
    except Exception:
        return False
    return False


def fetch_html(url: str) -> str:
    try:
        r = SESSION.get(url, timeout=25)
        r.raise_for_status()
        return r.text
    except Exception:
        return ""


def find_pdf_generic(html: str, base_url: str) -> str:
    if BeautifulSoup:
        soup = BeautifulSoup(html, "html.parser")
        m = soup.find("meta", attrs={"name": re.compile(r"^citation_pdf_url$", re.I)})
        if m and m.get("content"):
            return urljoin(base_url, m["content"].strip())
        l = soup.find("link", attrs={"type": re.compile(r"application/pdf", re.I)})
        if l and l.get("href"):
            return urljoin(base_url, l["href"].strip())
        cands = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            txt = (a.get_text() or "").lower()
            full = urljoin(base_url, href)
            if href.lower().endswith(".pdf"):
                cands.append(full)
            elif "pdf" in txt or "download" in txt:
                cands.append(full)
        cands = list(dict.fromkeys(cands))
        cands.sort(key=lambda u: (0 if u.lower().endswith(".pdf") else 1, len(u)))
        return cands[0] if cands else ""
    # Regex fallback
    m = re.search(r'citation_pdf_url"\s*content="([^"]+)"', html, flags=re.I)
    if m:
        return urljoin(base_url, m.group(1))
    m = re.search(r'href="([^"]+\.pdf)"', html, flags=re.I)
    return urljoin(base_url, m.group(1)) if m else ""


def find_pdf_domain(html: str, base_url: str) -> str:
    host = urlparse(base_url).netloc.lower()
    # Springer
    if "springer" in host:
        m = re.search(r'href="(/content/pdf/[^"]+\.pdf)"', html, flags=re.I)
        if m:
            return urljoin(base_url, m.group(1))
        m = re.search(r'"pdfUrl"\s*:\s*"([^"]+)"', html)
        if m:
            return urljoin(base_url, m.group(1))
    # Wiley
    if "wiley.com" in host:
        m = re.search(r'href="(/doi/(?:pdfdirect|pdf)/[^"]+)"', html, flags=re.I)
        if m:
            return urljoin(base_url, m.group(1))
        m = re.search(r'citation_pdf_url"\s*content="([^"]+)"', html, flags=re.I)
        if m:
            return urljoin(base_url, m.group(1))
    # ACS
    if "pubs.acs.org" in host:
        m = re.search(r'href="(/doi/(?:pdf|epdf)/[^"]+)"', html, flags=re.I)
        if m:
            return urljoin(base_url, m.group(1))
        m = re.search(r'citation_pdf_url"\s*content="([^"]+)"', html, flags=re.I)
        if m:
            return urljoin(base_url, m.group(1))
    # RSC
    if "rsc.org" in host:
        m = re.search(r'href="([^"]+/(?:articlepdf|content/articlepdf)/[^"]+\.pdf)"', html, flags=re.I)
        if m:
            return urljoin(base_url, m.group(1))
    # MDPI
    if "mdpi.com" in host:
        m = re.search(r'href="([^"]+/pdf(?:\?[^"]*)?)"', html, flags=re.I)
        if m:
            return urljoin(base_url, m.group(1))
        m = re.search(r'href="([^"]+/pdf-download[^"]*)"', html, flags=re.I)
        if m:
            return urljoin(base_url, m.group(1))
    # IEEE Xplore
    if "ieeexplore.ieee.org" in host:
        m = re.search(r'href="(/stamp/stamp\.jsp[^"]+)"', html, flags=re.I)
        if m:
            return urljoin(base_url, m.group(1))
    # Nature
    if "nature.com" in host:
        m = re.search(r'href="([^"]+\.pdf)"[^>]*data-track-action="download pdf"', html, flags=re.I)
        if m:
            return urljoin(base_url, m.group(1))
    # ScienceDirect
    if "sciencedirect.com" in host:
        m = re.search(r'"pdfDownload"\s*:\s*\{\s*"url"\s*:\s*"([^"]+)"', html, flags=re.I | re.S)
        if m:
            return urljoin(base_url, m.group(1))
        m = re.search(r'href="([^"]+/pdf(?:ft)?[^"]*)"', html, flags=re.I)
        if m:
            return urljoin(base_url, m.group(1))
    # PMC
    if "ncbi.nlm.nih.gov" in host and "/pmc/articles/" in base_url:
        return base_url.rstrip("/") + "/pdf"
    return ""


def pick_pdf_from(landing_url: str, doi: Optional[str], validate=True) -> str:
    # Try Unpaywall again (sometimes it backfills)
    if doi:
        try:
            j = _get_json(f"https://api.unpaywall.org/v2/{doi}", params={"email": UNPAYWALL_EMAIL})
            loc = j.get("best_oa_location") or j.get("oa_location")
            cand = (loc or {}).get("url_for_pdf") or (loc or {}).get("url")
            if cand and (not validate or validate_pdf(cand, validate=True)):
                return cand
        except Exception:
            pass
    landing = (landing_url or "").strip()
    if not landing and doi:
        landing = resolve_doi(doi)
    if not landing:
        return ""
    html = fetch_html(landing)
    if not html:
        return ""
    cand = find_pdf_domain(html, landing) or find_pdf_generic(html, landing)
    if cand:
        if not validate or validate_pdf(cand, validate=True) or cand.lower().endswith(".pdf"):
            return cand
    # PubMed → PMC
    if "pubmed.ncbi.nlm.nih.gov" in landing:
        m = re.search(
            r'href="(https://www\.ncbi\.nlm\.nih\.gov/pmc/articles/[^"]+)"', html, flags=re.I
        )
        if m:
            pmc = m.group(1)
            pdf = pmc.rstrip("/") + "/pdf"
            if not validate or validate_pdf(pdf, validate=True):
                return pdf
    return ""


def enrich_from_landing(
    recs: List[Record], *, validate: bool = True, sleep: float = 0.5, verbose: bool = False
) -> int:
    """Second-pass PDF fill: for records missing pdf_url but having url/doi."""
    to_fill = [r for r in recs if not (r.pdf_url or "") and ((r.url or "") or (r.doi or ""))]
    print(f"[info] Second-pass PDF enrichment candidates: {len(to_fill)}")
    filled = 0
    for r in to_fill:
        pdf = pick_pdf_from(r.url or "", r.doi, validate=validate)
        if pdf:
            r.pdf_url = pdf
            filled += 1
            if verbose:
                print(f"  [+] {r.title[:80]} → {pdf}")
        time.sleep(sleep)
    print(f"[ok] Second-pass filled {filled} pdf_url fields")
    return filled


# --------------------------- Deduplication ---------------------------

def deduplicate(records: List[Record]) -> List[Record]:
    by_doi: Dict[str, Record] = {}
    out: List[Record] = []
    for r in records:
        if r.doi:
            if r.doi not in by_doi:
                by_doi[r.doi] = r
            else:
                cur = by_doi[r.doi]
                if (not cur.pdf_url and r.pdf_url) or (len(r.abstract) > len(cur.abstract)):
                    by_doi[r.doi] = r
        else:
            out.append(r)
    merged = list(by_doi.values()) + out
    if not fuzz:
        seen = set()
        uniq: List[Record] = []
        for r in merged:
            key = re.sub(r"\W+", "", r.title.lower())[:80]
            if key in seen:
                continue
            seen.add(key)
            uniq.append(r)
        return uniq
    keys: List[str] = []
    uniq2: List[Record] = []
    for r in merged:
        key = re.sub(r"\W+", "", r.title.lower())
        if not keys:
            keys.append(key)
            uniq2.append(r)
            continue
        bucket_idx = [i for i, k in enumerate(keys) if k[:12] == key[:12]]
        if not bucket_idx:
            sample_idx = random.sample(range(len(keys)), k=min(30, len(keys))) if keys else []
            bucket_idx = sample_idx
        if bucket_idx:
            candidates = [keys[i] for i in bucket_idx]
            best = rf_process.extractOne(key, candidates, scorer=fuzz.QRatio)
            if best and best[1] >= 95:
                continue
        keys.append(key)
        uniq2.append(r)
    return uniq2


# --------------------------- Pipeline ---------------------------

SOURCES = [
    ("OpenAlex", search_openalex),
    ("Crossref", search_crossref),
    ("arXiv", search_arxiv),
    ("SemanticScholar", search_semantic_scholar),
    ("DOAJ", search_doaj),
    ("PubMed", search_pubmed),
    ("Springer", search_springer),
    ("ScienceDirect", search_sciencedirect),
    ("IEEE Xplore", search_ieee),
]


def run(
    query: str,
    y0: int,
    y1: int,
    max_per_source: int,
    strict: bool,
    out_jsonl: str,
    out_csv: Optional[str],
    *,
    validate_pdf_links: bool = True,
) -> None:
    all_recs: List[Record] = []
    for name, func in SOURCES:
        try:
            print(f"[info] Querying {name}...")
            recs = func(query, y0, y1, max_per_source)
            print(f"[info] {name}: {len(recs)} records")
            all_recs.extend(recs)
        except Exception as e:
            print(f"[warn] {name} failed: {e}")
        time.sleep(random.uniform(0.1, 0.3))

    # Enrichment passes
    enrich_unpaywall(all_recs)  # pass 1
    enrich_from_landing(all_recs, validate=validate_pdf_links, sleep=0.5, verbose=False)  # pass 2

    # Score + strict filter
    for r in all_recs:
        r.score = score_record(r, query)
    if strict:
        all_recs = [r for r in all_recs if r.score >= 2.0]

    # Dedupe
    all_recs = deduplicate(all_recs)

    # Order
    all_recs.sort(key=lambda r: (-(r.score or 0), -(r.year or 0), r.title.lower()))

    # Write JSONL
    os.makedirs(os.path.dirname(out_jsonl) or ".", exist_ok=True)
    with open(out_jsonl, "w", encoding="utf-8") as f:
        for r in all_recs:
            f.write(json.dumps(r.to_dict(), ensure_ascii=False) + "\n")
    print(f"[ok] Wrote {len(all_recs)} records to {out_jsonl}")

    # Write CSV
    if out_csv:
        os.makedirs(os.path.dirname(out_csv) or ".", exist_ok=True)
        fields = [
            "title",
            "abstract",
            "year",
            "doi",
            "url",
            "pdf_url",
            "venue",
            "authors",
            "source",
            "_score",
        ]
        with open(out_csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for r in all_recs:
                row = r.to_dict()
                row["authors"] = "; ".join(row.get("authors") or [])
                w.writerow(row)
        print(f"[ok] Wrote CSV to {out_csv}")


# --------------------------- CLI ---------------------------

def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Search materials-science papers by topic, enrich PDFs (Unpaywall + scraping), "
                    "dedupe, and export (no PDF downloading)."
    )
    ap.add_argument("--query", required=True, help="Topic keywords (materials science domain)")
    ap.add_argument("--from-year", type=int, default=2000)
    ap.add_argument("--to-year", type=int, default=datetime.now(timezone.utc).year)
    ap.add_argument("--max-per-source", type=int, default=200, help="Maximum records to fetch per source")
    ap.add_argument("--strict", action="store_true", help="Keep only records with sufficient materials relevance")
    ap.add_argument("--out", default="materials_results.jsonl", help="Output JSONL filename")
    ap.add_argument("--csv", default=None, help="Optional CSV filename")
    ap.add_argument(
        "--no-validate",
        action="store_true",
        help="Do not strictly validate candidate PDF links (HEAD/GET sniff) during scraping pass",
    )
    return ap.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> None:
    args = parse_args(argv)
    if args.from_year > args.to_year:
        print("[error] --from-year must be <= --to-year", file=sys.stderr)
        sys.exit(2)
    run(
        query=args.query,
        y0=args.from_year,
        y1=args.to_year,
        max_per_source=args.max_per_source,
        strict=args.strict,
        out_jsonl=args.out,
        out_csv=args.csv,
        validate_pdf_links=(not args.no_validate),
    )


if __name__ == "__main__":
    main()
