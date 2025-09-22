# 📖 Materials Science Papers Harvester — Hosted App Guide

This is the **user guide** for the deployed Streamlit app. It focuses on how to use the hosted version, not on development or deployment.

---

## 🌐 Access

Open the app in your browser at:

👉 **https://ms-paper-harvester.streamlit.app/**

---

## 🔎 How to Use the App

1. **Enter your query**

   * In the sidebar, type topic keywords (e.g., `perovskite thin films defect passivation`).

2. **Set filters**

   * Choose the year range with the slider.
   * Adjust *max per source* if you want more/less results.
   * Enable **Strict mode** to keep only records strongly relevant to materials science.

3. **Run the search**

   * Click **🚀 Start harvest**.
   * Progress and logs will show in the main panel.

4. **Preview results**

   * After the run, the newest CSV file is shown in a table.
   * You can scroll and inspect metadata like title, abstract, DOI, PDF link, etc.

5. **Download CSV**

   * Use the **⬇️ Download CSV** button to save the full table to your computer.

6. **Download PDFs**

   * Scroll to the **Download PDFs** section.
   * The app will:

     * Fetch all valid `pdf_url` links from the CSV.
     * Verify each file.
     * Bundle them into a single ZIP.
   * Click **⬇️ Download all PDFs as ZIP** to save them directly to your laptop.
   * If some files fail, you can download `failed_downloads.csv` to see which ones.

---

## 📦 Outputs

* **CSV file** — all harvested metadata.
* **ZIP file** — all available PDFs, bundled for easy download.
* **failed\_downloads.csv** — list of papers that did not yield a valid PDF.

---

## ⚠️ Notes for Users

* Results depend on availability in public APIs; not all papers will have open-access PDFs.
* Files are stored temporarily in the app’s cloud environment — always use the **Download** buttons to save them locally.
* Large queries may take several minutes, especially if many sources are polled.

---

## 🙋 FAQ

* **Q: Why are some PDFs missing?**
  A: Not all publishers provide open-access copies. If a paper is paywalled, the PDF link will be empty.

* **Q: How do I get more results?**
  A: Increase *max per source* in the sidebar, but note that APIs often have rate limits.

* **Q: Is my email needed?**
  A: The app already identifies itself to APIs using a built-in contact email. You don’t need to provide one.

---

*Enjoy exploring materials science literature!*
