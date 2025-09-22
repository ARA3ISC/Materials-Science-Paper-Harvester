# 🔬 Materials Science Papers Harvester

A Streamlit web app to **search, aggregate, and download scientific papers in materials science**.
It queries multiple popular sources (OpenAlex, Crossref, arXiv, Semantic Scholar, DOAJ, PubMed, Springer, Elsevier/ScienceDirect, IEEE Xplore), deduplicates results, enriches with Unpaywall and landing-page scraping to recover missing PDF URLs, and exports clean JSONL/CSV files. Users can preview results and **download all available PDFs as a single ZIP** to their local computer.

---

## ✨ Features

* Search by **topic keywords** and year range.
* Aggregate results from many literature APIs.
* Normalize records into a consistent schema.
* Deduplicate using DOI and fuzzy title matching.
* Enrich missing PDF URLs via Unpaywall + landing-page scraping.
* Export to **CSV** and **JSONL**.
* **Streamlit UI**:

  * Logs and progress indicators.
  * Preview CSV in-browser.
  * One-click **download of all PDFs as a ZIP**.
  * Download failures log (for missing/broken PDFs).

---

## 📂 Project Structure

```
.
├── app.py                        # Streamlit UI
├── materials_papers_harvester.py # Main harvester (no PDF download)
├── download_verified_pdfs.py     # Bulk PDF downloader + verification
├── requirements.txt              # Python dependencies
├── .gitignore                    # Ignore venv + outputs
├── .streamlit/
│   └── secrets.toml              # Local secrets (API keys, optional)
└── runs/                         # Created at runtime, holds outputs
```

---

## ⚙️ Requirements

* Python 3.9+
* Packages listed in `requirements.txt`:

  ```
  streamlit
  pandas
  requests
  beautifulsoup4
  tenacity
  rapidfuzz
  pypdf
  ```

---

## 🚀 Running Locally

1. Clone the repo:

   ```bash
   git clone https://github.com/your-username/materials-harvester.git
   cd materials-harvester
   ```

2. Create a virtual environment:

   ```bash
   python -m venv venv
   source venv/bin/activate     # Windows: venv\Scripts\activate
   ```

3. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

4. Add your API keys (see below) in `.streamlit/secrets.toml`:

   ```toml
   SPRINGER_API_KEY = "your-springer-meta-key"
   ELSEVIER_API_KEY = "your-elsevier-key"
   IEEE_API_KEY = "your-ieee-key"
   CROSSREF_EMAIL = "your.email@domain.com"
   EMAIL = "your.email@domain.com"
   SEMANTIC_SCHOLAR_API_KEY = "optional-semantic-key"
   ```

5. Run the app:

   ```bash
   streamlit run app.py
   ```

Open [http://localhost:8501](http://localhost:8501) in your browser.

---

## 🔑 API Keys / Secrets

* **Springer Metadata API** → `SPRINGER_API_KEY` (use Meta API key)
* **Elsevier (ScienceDirect) API** → `ELSEVIER_API_KEY`
* **IEEE Xplore API** → `IEEE_API_KEY`
* **Crossref & PubMed** → set your email for `CROSSREF_EMAIL` and `EMAIL`
* **Semantic Scholar API** (optional) → `SEMANTIC_SCHOLAR_API_KEY`

### On Streamlit Cloud

Set secrets in the app settings (App page → ⋮ → Settings → Secrets). Paste JSON like:

```json
{
  "SPRINGER_API_KEY": "xxxxx",
  "ELSEVIER_API_KEY": "xxxxx",
  "IEEE_API_KEY": "xxxxx",
  "CROSSREF_EMAIL": "you@domain.com",
  "EMAIL": "you@domain.com",
  "SEMANTIC_SCHOLAR_API_KEY": "optional"
}
```

---

## ☁️ Deploying on Streamlit Cloud

1. Push this repo to GitHub.
2. Go to [https://share.streamlit.io](https://share.streamlit.io) and click **New app**.
3. Select your repo/branch and set `app.py` as the entrypoint.
4. Add API keys in **Secrets** (see above).
5. The app will rebuild and launch automatically.

---

## 📦 Outputs

* **CSV**: Tabular file with metadata and PDF URLs.
* **JSONL**: JSON lines file for programmatic analysis.
* **ZIP**: All downloaded PDFs (optional) to your laptop.
* **failed\_downloads.csv**: Records with broken/missing PDFs.

---

## 🧭 Usage Notes

* Files saved in the cloud environment are temporary. Use the **Download CSV** or **Download ZIP** buttons to save them locally.
* Respect API rate limits — use your keys and institutional email where required.
* The harvester does not bypass paywalls: PDF download succeeds only if a valid open-access link is available.
* Unpaywall email is set to `mohamed.aneddame-ext@um6p.ma` by default in the code (change if needed).

---

## 🛠 Troubleshooting

* **App build fails** → verify `requirements.txt` contains every package you import.
* **Missing results from a source** → ensure corresponding API key is set in secrets and the API quota isn’t exhausted.
* **Downloads not appearing locally when deployed** → the app stores files in the container; use the ZIP download button to save to your laptop.
* **OpenAlex/DOAJ errors** → the code uses corrected endpoints and parameters; ensure you’re running the latest `materials_papers_harvester.py`.

---

## 🙏 Attribution

This project queries and aggregates metadata from multiple scholarly APIs and respects each provider’s terms of service:

* OpenAlex, Crossref, arXiv, Semantic Scholar, DOAJ, PubMed/NCBI E-utilities, Springer Metadata API, Elsevier/ScienceDirect API, IEEE Xplore.

---

## 📬 Contact / Contributions

If you find bugs or want to contribute, please open an issue or pull request on the GitHub repo. For questions about API usage, include log output and the query you used.

---

*Happy harvesting!*
