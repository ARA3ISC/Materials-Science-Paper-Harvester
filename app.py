# app.py — Streamlit UI for materials_papers_harvester.py + CSV preview + one-click PDF download
import streamlit as st
import subprocess, sys, time
from pathlib import Path
import pandas as pd

st.set_page_config(page_title="Materials Harvester", layout="wide")
st.title("🔬 Materials Science Paper Harvester")
st.caption("Run the harvester to create a CSV, preview it, and (optionally) download all PDFs listed in the CSV.")

# ---- Fixed locations ---------------------------------------------------------
WORKDIR = Path("runs/materials_harvest")
WORKDIR.mkdir(parents=True, exist_ok=True)

SCRIPT_PATH = (Path(__file__).parent / "materials_papers_harvester.py").resolve()
DOWNLOADER_SCRIPT = (Path(__file__).parent / "download_verified_pdfs.py").resolve()  # external downloader

# ---- Sidebar ----------------------------------------------------------------
with st.sidebar:
    st.header("🔎 Search Parameters")
    query = st.text_input("Topic keywords (materials science)",
                          value="perovskite thin films defect passivation")
    year_min, year_max = st.slider("Year range", 1990, 2035, (2005, 2025))
    max_per_source = st.number_input("Max per source", 1, 10000, 200, step=10)

    st.header("⚙️ Options")
    strict = st.checkbox("Strict materials relevance filter", value=True)

    st.header("📦 Outputs")
    out_base = st.text_input("Output base filename (no path)", value="materials_results")
    write_csv = st.checkbox("Also write CSV", value=True)

# ---- Utilities ---------------------------------------------------------------
def list_files(root: Path):
    return {str(p) for p in root.rglob("*") if p.is_file()}

def newest_csv(root: Path):
    cs = list(Path(root).rglob("*.csv"))
    return max(cs, key=lambda p: p.stat().st_mtime) if cs else None

# ---- Run controls ------------------------------------------------------------
st.subheader("Run harvester")
if "cancel" not in st.session_state:
    st.session_state.cancel = False

def request_cancel():
    st.session_state.cancel = True

col_run, col_cancel = st.columns([1, 1])
with col_run:
    run = st.button("🚀 Start harvest", type="primary", use_container_width=True)
with col_cancel:
    st.button("🛑 Cancel", on_click=request_cancel, use_container_width=True)

status_box = st.empty()
progress_bar = st.progress(0)
elapsed_ph = st.empty()
log_box = st.empty()

csv_path = None

if run:
    if not SCRIPT_PATH.exists():
        st.error(f"Script not found: {SCRIPT_PATH}")
    else:
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

        # Preview newest CSV (if any)
        csv_path = newest_csv(WORKDIR)
        if csv_path:
            st.subheader("Preview newest CSV")
            st.caption(str(csv_path))
            try:
                df = pd.read_csv(csv_path)
                st.dataframe(df, use_container_width=True, hide_index=True)
                st.download_button(
                    "⬇️ Download CSV",
                    data=df.to_csv(index=False).encode(),
                    file_name=csv_path.name,
                    mime="text/csv"
                )
            except Exception as e:
                st.warning(f"Could not read CSV: {e}")

# ---- Download PDFs from CSV --------------------------------------------------
st.subheader("⬇️ Download PDFs listed in a CSV")
csv_default = str(csv_path) if csv_path else (str(newest_csv(WORKDIR)) if newest_csv(WORKDIR) else "")
dl_csv = st.text_input("CSV path (must contain a 'pdf_url' column)", value=csv_default)
pdf_outdir = st.text_input("Output folder", value="runs/pdfs")
skip_existing = st.checkbox("Skip existing valid PDFs", value=True)

dl_btn = st.button("Start PDF download", type="primary")
if dl_btn:
    if not DOWNLOADER_SCRIPT.exists():
        st.error(f"Downloader not found: {DOWNLOADER_SCRIPT}")
    elif not dl_csv or not Path(dl_csv).exists():
        st.error("CSV path is empty or does not exist.")
    else:
        Path(pdf_outdir).mkdir(parents=True, exist_ok=True)
        cmd_dl = [
            sys.executable, str(DOWNLOADER_SCRIPT),
            "--in", dl_csv,
            "--outdir", pdf_outdir,
        ]
        if skip_existing:
            cmd_dl.append("--skip-existing")

        with st.status("Downloading PDFs…", expanded=True) as s:
            try:
                # Run in project root so relative paths behave as expected
                proc_dl = subprocess.run(cmd_dl, text=True, capture_output=True, cwd=str(Path(__file__).parent))
                st.code(proc_dl.stdout or "(no output)")
                if proc_dl.returncode == 0:
                    s.update(label="Completed ✔️", state="complete")
                    st.success(f"PDFs saved under: {pdf_outdir}")
                    fail_log = Path("failed_downloads.csv")
                    if fail_log.exists():
                        st.download_button(
                            "⬇️ Download failures CSV",
                            data=fail_log.read_bytes(),
                            file_name="failed_downloads.csv",
                            mime="text/csv"
                        )
                else:
                    s.update(label="Failed", state="error")
                    st.error(proc_dl.stderr or "Downloader returned non-zero exit code.")
            except Exception as e:
                s.update(label="Error", state="error")
                st.exception(e)

st.markdown("---")
st.caption("Tip: the downloader verifies each PDF (header/footer; optional pypdf parse) and logs failures to failed_downloads.csv.")
