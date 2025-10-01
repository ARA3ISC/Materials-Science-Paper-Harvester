# app.py — Streamlit UI for materials_papers_harvester.py + CSV preview + bulk PDF download (ZIP to user)
import streamlit as st
import subprocess, sys, time, io, zipfile, shutil
from pathlib import Path
import pandas as pd

st.set_page_config(page_title="Materials Harvester", layout="wide")
st.title("🔬 Materials Science Paper Harvester")
st.caption("Run the harvester, preview CSV, and (after a successful run) download all PDFs as a ZIP to your laptop.")

# ---- Fixed locations ---------------------------------------------------------
ROOT = Path(__file__).parent.resolve()
WORKDIR = ROOT / "runs" / "materials_harvest"
WORKDIR.mkdir(parents=True, exist_ok=True)

SCRIPT_PATH = ROOT / "materials_papers_harvester.py"
DOWNLOADER_SCRIPT = ROOT / "download_verified_pdfs.py"   # external downloader

# ---- Session flags -----------------------------------------------------------
if "cancel" not in st.session_state:
    st.session_state.cancel = False
if "harvest_done" not in st.session_state:
    st.session_state.harvest_done = False
if "last_csv_path" not in st.session_state:
    st.session_state.last_csv_path = ""

# ---- Sidebar ----------------------------------------------------------------
with st.sidebar:
    st.header("🔎 Search Parameters")
    # Query must be empty on load and required to run
    query = st.text_input(
        "Topic keywords (materials science) — required",
        value="",
        placeholder="e.g., perovskite thin films defect passivation",
        key="query_input",
    )

    year_min, year_max = st.slider("Year range", 1990, 2035, (2005, 2025))
    max_per_source = st.number_input("Max per source", 1, 10000, 200, step=10)

    st.header("⚙️ Options")
    strict = st.checkbox("Strict materials relevance filter", value=True)
    no_validate = st.checkbox(
        "Speed up PDF link sniff (disable strict validate)",
        value=False,
        help="Skips HEAD/GET checks during PDF enrichment. Faster but may include some bad links."
    )

    st.header("📦 Outputs")
    out_base = st.text_input("Output base filename (no path)", value="materials_results")
    write_csv = st.checkbox("Also write CSV", value=True)

# ---- Utilities ---------------------------------------------------------------
def list_files(root: Path):
    return {str(p) for p in root.rglob("*") if p.is_file()}

def newest_csv(root: Path):
    cs = list(Path(root).rglob("*.csv"))
    return max(cs, key=lambda p: p.stat().st_mtime) if cs else None

def zip_dir_in_memory(base_dir: Path) -> io.BytesIO:
    """Zip a directory (only PDFs) into a BytesIO for download_button."""
    mem = io.BytesIO()
    with zipfile.ZipFile(mem, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(base_dir.rglob("*.pdf")):
            if p.is_file():
                zf.write(p, arcname=str(p.relative_to(base_dir)))
    mem.seek(0)
    return mem

# ---- Run controls ------------------------------------------------------------
st.subheader("Run harvester")

def request_cancel():
    st.session_state.cancel = True

col_run, col_cancel = st.columns([1, 1])
with col_run:
    run = st.button(
        "🚀 Start harvest",
        type="primary",
        use_container_width=True,
        disabled=(len((query or "").strip()) == 0),
    )
with col_cancel:
    st.button("🛑 Cancel", on_click=request_cancel, use_container_width=True)

status_box = st.empty()
progress_bar = st.progress(0)
elapsed_ph = st.empty()
log_box = st.empty()

csv_path = None

if run:
    # safety check
    if not (query or "").strip():
        st.error("Please enter a topic in the query box (it is required).")
    elif not SCRIPT_PATH.exists():
        st.error(f"Script not found: {SCRIPT_PATH}")
    else:
        # reset run flags at start
        st.session_state.harvest_done = False
        st.session_state.last_csv_path = ""
        st.session_state.cancel = False

        before = list_files(WORKDIR)
        start = time.time()

        with st.status("Running harvester…", state="running", expanded=True) as status:
            jsonl_name = f"{out_base}.jsonl"
            csv_name = f"{out_base}.csv" if write_csv else None

            cmd = [
                sys.executable, str(SCRIPT_PATH),
                "--query", query,
                "--from-year", str(year_min),
                "--to-year", str(year_max),
                "--max-per-source", str(max_per_source),
                "--out", jsonl_name,
            ]
            if csv_name:
                cmd += ["--csv", csv_name]
            if strict:
                cmd.append("--strict")
            if no_validate:
                cmd.append("--no-validate")

            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    cwd=str(WORKDIR)
                )
            except Exception as e:
                status.update(label="Failed to launch process.", state="error")
                st.exception(e)
            else:
                lines = []
                pseudo = 5
                last_tick = time.time()
                while True:
                    line = proc.stdout.readline()
                    if line == "" and proc.poll() is not None:
                        break
                    if line:
                        lines.append(line.rstrip("\n"))
                        log_box.code("\n".join(lines[-800:]))
                        pseudo = min(95, pseudo + 1)
                        progress_bar.progress(pseudo)

                    now = time.time()
                    if now - last_tick >= 1:
                        elapsed = int(now - start)
                        elapsed_ph.info(f"⏱️ Elapsed: {elapsed}s  •  Working directory: `{WORKDIR}`")
                        if line == "":
                            pseudo = min(95, pseudo + 1)
                            progress_bar.progress(pseudo)
                        last_tick = now

                    if st.session_state.cancel:
                        try:
                            proc.terminate()
                        except Exception:
                            pass
                        status.update(label="Cancelled by user.", state="error", expanded=True)
                        break

                ret = proc.wait()
                if not st.session_state.cancel:
                    if ret == 0:
                        progress_bar.progress(100)
                        status.update(label="Completed ✔️", state="complete", expanded=False)
                        st.success("✅ Harvest finished.")
                    else:
                        status.update(label=f"Exited with code {ret}", state="error", expanded=True)
                        st.error(f"❌ Exit code {ret} — check logs above.")

        # Show new files
        after = list_files(WORKDIR)
        new_files = sorted(after - before)
        st.subheader("New files created")
        if new_files:
            st.write("\n".join(new_files[:200]))
        else:
            st.info("No new files detected.")

        # Preview newest CSV (if any) and mark harvest_done only if non-empty
        csv_path = newest_csv(WORKDIR)
        if csv_path:
            st.subheader("Preview newest CSV")
            st.caption(str(csv_path))
            try:
                df = pd.read_csv(csv_path)
                if df.empty:
                    st.warning("The newest CSV is empty.")
                else:
                    # mark success for download section
                    st.session_state.harvest_done = True
                    st.session_state.last_csv_path = str(csv_path)

                    st.dataframe(df, use_container_width=True, hide_index=True)
                    st.download_button(
                        "⬇️ Download CSV",
                        data=df.to_csv(index=False).encode(),
                        file_name=csv_path.name,
                        mime="text/csv"
                    )
            except Exception as e:
                st.warning(f"Could not read CSV: {e}")
        else:
            st.info("No CSV found yet.")

# ---- Download PDFs section: ONLY show after successful harvest with data ----
if st.session_state.harvest_done and st.session_state.last_csv_path:
    st.subheader("⬇️ Download PDFs listed in the latest CSV (as a ZIP to your computer)")

    # Use the last successful CSV path from this session/run
    dl_csv = st.text_input(
        "CSV path (must contain a 'pdf_url' column)",
        value=st.session_state.last_csv_path,
    )
    pdf_outdir = st.text_input("Temporary download folder (inside app sandbox)", value="runs/pdfs")
    skip_existing = st.checkbox("Skip existing valid PDFs", value=True)
    make_zip = st.checkbox("Bundle into a single ZIP for download", value=True)

    col_a, col_b = st.columns([1,1])
    with col_a:
        dl_btn = st.button("Start PDF download", type="primary", use_container_width=True)
    with col_b:
        cleanup_btn = st.button("🧹 Clear all results", use_container_width=True)

    if cleanup_btn:
        try:
            # remove both PDFs and harvested results
            pdf_dir = ROOT / pdf_outdir
            harvest_dir = WORKDIR
            if pdf_dir.exists():
                shutil.rmtree(pdf_dir)
            if harvest_dir.exists():
                shutil.rmtree(harvest_dir)
            # recreate empty harvest dir so app still works
            harvest_dir.mkdir(parents=True, exist_ok=True)
            st.success("✅ Cleared all stored results (CSVs and PDFs) from the server.")
            # reset flags so download section hides again
            st.session_state.harvest_done = False
            st.session_state.last_csv_path = ""
        except Exception as e:
            st.warning(f"Could not clear data: {e}")

    if dl_btn:
        if not DOWNLOADER_SCRIPT.exists():
            st.error(f"Downloader not found: {DOWNLOADER_SCRIPT}")
        elif not dl_csv or not Path(dl_csv).exists():
            st.error("CSV path is empty or does not exist.")
        else:
            outdir_abs = ROOT / pdf_outdir
            outdir_abs.mkdir(parents=True, exist_ok=True)
            cmd_dl = [
                sys.executable, str(DOWNLOADER_SCRIPT),
                "--in", str(Path(dl_csv).resolve()),
                "--outdir", str(outdir_abs),
            ]
            if skip_existing:
                cmd_dl.append("--skip-existing")

            with st.status("Downloading PDFs…", expanded=True) as s:
                try:
                    proc_dl = subprocess.run(cmd_dl, text=True, capture_output=True, cwd=str(ROOT))
                    st.code(proc_dl.stdout or "(no output)")
                    if proc_dl.returncode == 0:
                        s.update(label="Completed ✔️", state="complete")
                        # Count PDFs
                        files = sorted([p for p in outdir_abs.rglob("*.pdf") if p.is_file()])
                        st.success(f"Downloaded **{len(files)}** PDF(s) to temporary folder: `{outdir_abs}`")

                        # Offer failures CSV (if any)
                        fail_log = ROOT / "failed_downloads.csv"
                        if fail_log.exists():
                            st.download_button(
                                "⬇️ Download failures CSV",
                                data=fail_log.read_bytes(),
                                file_name="failed_downloads.csv",
                                mime="text/csv"
                            )

                        # Offer ZIP to user
                        if make_zip and files:
                            zip_bytes = zip_dir_in_memory(outdir_abs)
                            st.download_button(
                                "⬇️ Download all PDFs as ZIP",
                                data=zip_bytes,
                                file_name="materials_pdfs.zip",
                                mime="application/zip"
                            )

                            with st.expander("Show individual files"):
                                for p in files[:200]:
                                    st.write(f"- {p.name}")

                    else:
                        s.update(label="Failed", state="error")
                        st.error(proc_dl.stderr or "Downloader returned non-zero exit code.")
                except Exception as e:
                    s.update(label="Error", state="error")
                    st.exception(e)

else:
    # When not yet done, gently hint why the section is hidden
    st.info("Run a harvest with a non-empty result first. The PDF download section will appear here after completion.")

st.markdown("---")
st.caption(
    "Notes: On Streamlit Cloud, files are stored temporarily in the app sandbox. "
    "Use the ZIP button to save all PDFs to your local computer once harvesting is complete."
)

import streamlit as st
st.write(st.secrets.get("api_key", "No key in st.secrets"))
