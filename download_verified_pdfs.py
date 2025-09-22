#!/usr/bin/env python3
"""
download_verified_pdfs.py  — download all pdf_url entries and verify integrity.

CSV schema expected (minimum): pdf_url
Recommended columns: [title, page_url, pdf_url]  (others are ignored)

What it does:
- For each row with non-empty pdf_url, fetch the URL and stream-save to --outdir.
- Accepts only files that look like real PDFs.
- Verifies each file after download:
    * header contains %PDF near the start
    * trailer contains %%EOF near the end
    * (optional) tries to open with pypdf if installed
- On any failure (HTTP error, not a PDF, truncated, validation error), logs the row to failed_downloads.csv.

Usage:
  pip install requests
  # optional: pip install pypdf  (for an extra validation pass)
  python download_verified_pdfs.py --in sources.csv --outdir pdfs \
    --cookies cookies_scidir.txt --skip-existing

Notes:
- This script DOES NOT try to "fix/repair/extract" a PDF link from HTML pages.
  It ONLY downloads from the given pdf_url and verifies integrity.
"""

import argparse
import csv
import os
import re
import sys
from html import unescape
from urllib.parse import urlsplit, unquote

import requests
from http.cookiejar import MozillaCookieJar

# ---- optional deep validation ----
try:
    from pypdf import PdfReader  # pip install pypdf
    HAVE_PYPDF = True
except Exception:
    HAVE_PYPDF = False

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")

CHUNK = 64 * 1024  # 64 KB per chunk
PLACEHOLDERS = {"", "-", "n/a", "na", "null", "none", "nan"}

def present(v: str) -> bool:
    s = (v or "").strip()
    return s and s.lower() not in PLACEHOLDERS

def sanitize_filename(name: str, max_len: int = 150) -> str:
    name = unescape(name or "").strip()
    name = name.replace("\\", "_").replace("/", "_")
    name = re.sub(r"[^A-Za-z0-9\\-\\._ ]+", "_", name)
    name = re.sub(r"\\s+", "_", name).strip("_")
    if not name:
        name = "file"
    if len(name) > max_len:
        root, ext = os.path.splitext(name)
        name = root[: max_len - len(ext)] + ext
    return name

def filename_from_cd(cd: str) -> str:
    if not cd:
        return ""
    m = re.search(r'filename\*=(?:UTF-8\'\')?([^;]+)', cd, re.I)
    if m:
        return sanitize_filename(unquote(m.group(1)).strip('"\' '))
    m = re.search(r'filename=([^;]+)', cd, re.I)
    if m:
        return sanitize_filename(unquote(m.group(1)).strip('"\' '))
    return ""

def guess_filename(row: dict, final_url: str, resp: requests.Response) -> str:
    base = filename_from_cd(resp.headers.get("Content-Disposition", ""))
    if not base:
        seg = os.path.basename(urlsplit(final_url).path)
        seg = sanitize_filename(unquote(seg))
        base = seg or sanitize_filename((row.get("title") or row.get("doi") or "file"))
    if not base.lower().endswith(".pdf"):
        base += ".pdf"
    return base

def ensure_unique(outdir: str, filename: str) -> str:
    base, ext = os.path.splitext(filename)
    path = os.path.join(outdir, filename)
    i = 1
    while os.path.exists(path):
        path = os.path.join(outdir, f"{base}-{i}{ext}")
        i += 1
    return path

def make_session(cookies_path: str = None) -> requests.Session:
    s = requests.Session()
    adapter = requests.adapters.HTTPAdapter(max_retries=3, pool_connections=50, pool_maxsize=50)
    s.mount("http://", adapter); s.mount("https://", adapter)
    s.headers.update({"User-Agent": UA, "Accept": "application/pdf,*/*;q=0.8", "Connection": "close"})
    if cookies_path:
        cj = MozillaCookieJar()
        cj.load(cookies_path, ignore_discard=True, ignore_expires=True)
        s.cookies = cj
    return s

def first_kb_has_pdf_magic(b: bytes) -> bool:
    if not b:
        return False
    # Be tolerant: some servers can prepend a few bytes before "%PDF"
    head = b[:1024]
    return (b"%PDF" in head)

def ends_with_eof(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            if size < 10:
                return False
            tail = 4096 if size >= 4096 else size
            f.seek(-tail, os.SEEK_END)
            end_bytes = f.read()
        return (b"%%EOF" in end_bytes)
    except Exception:
        return False

def verify_pdf_file(path: str, deep: bool = True) -> (bool, str):
    # quick checks
    try:
        with open(path, "rb") as f:
            first = f.read(4096)
    except Exception as e:
        return False, f"read_error:{e.__class__.__name__}"

    if not first_kb_has_pdf_magic(first):
        return False, "no_pdf_header"

    if not ends_with_eof(path):
        return False, "missing_eof"

    if deep and HAVE_PYPDF:
        try:
            PdfReader(path)  # parse xref, etc.
        except Exception as e:
            return False, f"pypdf_parse_error:{e.__class__.__name__}"

    return True, "ok"

def download_one(session: requests.Session, row: dict, outdir: str,
                 connect_timeout: float, read_timeout: float,
                 skip_existing: bool) -> (bool, str, str):
    """
    Returns (ok, path_or_url, reason)
    ok=True: path_or_url is local file path
    ok=False: path_or_url is the original pdf_url
    """
    url = (row.get("pdf_url") or "").strip()
    if not present(url):
        return False, url, "empty_pdf_url"

    referer = (row.get("page_url") or "").strip() or None
    headers = {"Referer": referer} if referer else {}

    # GET (stream) with moderate timeouts; if the server is very slow this can still take a while
    try:
        r = session.get(url, timeout=(connect_timeout, read_timeout), allow_redirects=True, stream=True, headers=headers)
    except requests.RequestException as e:
        return False, url, f"http_error:{e.__class__.__name__}"

    if not (200 <= r.status_code < 400):
        return False, url, f"http_status:{r.status_code}"

    # Peek first chunk
    first = b""
    try:
        for chunk in r.iter_content(CHUNK):
            if chunk:
                first = chunk
                break
    except requests.RequestException as e:
        return False, url, f"stream_error:{e.__class__.__name__}"

    # Basic content-type / magic header check BEFORE writing a file
    ctype = (r.headers.get("Content-Type") or "").lower()
    looks_pdfish = ("application/pdf" in ctype) or first_kb_has_pdf_magic(first)
    if not looks_pdfish:
        r.close()
        return False, url, "not_pdf_response"

    # Decide filename and path
    final_url = r.url or url
    fname = guess_filename(row, final_url, r)
    path = os.path.join(outdir, fname)
    if skip_existing and os.path.exists(path):
        # Still verify the existing file; if it fails, we will write a new unique filename
        ok_existing, why = verify_pdf_file(path, deep=True)
        if ok_existing:
            return True, path, "exists_ok"
        else:
            path = ensure_unique(outdir, fname)  # write a fresh copy

    # Write file (include the first chunk we already read)
    try:
        with open(path, "wb") as f:
            if first:
                f.write(first)
            for chunk in r.iter_content(CHUNK):
                if chunk:
                    f.write(chunk)
    except Exception as e:
        r.close()
        try:
            if os.path.exists(path): os.remove(path)
        except OSError:
            pass
        return False, url, f"write_error:{e.__class__.__name__}"

    r.close()

    # Verify the saved file
    ok_file, reason = verify_pdf_file(path, deep=True)
    if not ok_file:
        try:
            os.remove(path)
        except OSError:
            pass
        return False, url, f"verify_fail:{reason}"

    return True, path, "ok"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_csv", required=True, help="Input CSV path")
    ap.add_argument("--outdir", default="pdfs", help="Output folder (default: pdfs)")
    ap.add_argument("--cookies", help="Path to Netscape cookies.txt (optional)")
    ap.add_argument("--connect-timeout", type=float, default=8.0, help="TCP connect timeout (s)")
    ap.add_argument("--read-timeout", type=float, default=20.0, help="Per-socket read timeout (s)")
    ap.add_argument("--skip-existing", action="store_true", help="Do not re-download if a same-named, valid PDF already exists")
    ap.add_argument("--fail-log", default="failed_downloads.csv", help="CSV file to write failures (default: failed_downloads.csv)")
    args = ap.parse_args()

    os.makedirs(args.outdir, exist_ok=True)

    # Build HTTP session
    session = make_session(args.cookies)

    # Load rows
    with open(args.in_csv, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        if "pdf_url" not in fieldnames:
            print("Error: CSV must contain 'pdf_url' column.", file=sys.stderr)
            sys.exit(1)
        have_title = "title" in fieldnames
        have_page = "page_url" in fieldnames
        rows = list(reader)

    # Prepare failure log
    fail_fields = ["title", "pdf_url", "page_url", "reason"]
    failed_rows = []

    total = 0
    ok = 0
    failed = 0

    print(f"Starting downloads… total rows: {len(rows)}\n", flush=True)

    for i, row in enumerate(rows, 1):
        pdf_url = (row.get("pdf_url") or "").strip()
        if not present(pdf_url):
            # Consider empty pdf_url a failure so you can handle it manually.
            failed += 1
            failed_rows.append({
                "title": row.get("title",""),
                "pdf_url": pdf_url,
                "page_url": row.get("page_url",""),
                "reason": "empty_pdf_url",
            })
            print(f"[{ok}/{failed} | {i}/{len(rows)}] × Empty pdf_url", flush=True)
            continue

        total += 1
        title_disp = (row.get("title") or "(no title)")[:80]
        print(f"[{i}/{len(rows)}] {title_disp}", flush=True)
        print(f"  → {pdf_url}", flush=True)

        success, path_or_url, reason = download_one(
            session, row, args.outdir,
            connect_timeout=args.connect_timeout,
            read_timeout=args.read_timeout,
            skip_existing=args.skip_existing
        )

        if success:
            ok += 1
            print(f"  ✓ Saved: {os.path.basename(path_or_url)}\n", flush=True)
        else:
            failed += 1
            failed_rows.append({
                "title": row.get("title",""),
                "pdf_url": pdf_url,
                "page_url": row.get("page_url",""),
                "reason": reason,
            })
            print(f"  × Failed: {reason}\n", flush=True)

    # Write failure log
    if failed_rows:
        with open(args.fail_log, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fail_fields)
            w.writeheader()
            w.writerows(failed_rows)

    print("Done.")
    print(f"Summary: Tried: {total} | Downloaded OK: {ok} | Failed: {failed}")
    if failed_rows:
        print(f"Failures saved to: {args.fail_log}")

if __name__ == "__main__":
    main()
