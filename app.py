import streamlit as st
import pandas as pd

st.set_page_config(
    page_title="Proactive Search",
    page_icon="🔍",
    layout="wide",
)


@st.cache_resource(show_spinner=False)
def _load_models():
    from parser_utils import load_models
    return load_models()


# ── Header ────────────────────────────────────────────────────────────────────
st.title("🔍 Proactive Search")
st.markdown(
    "Search PubMed for an author's publications and surface those linked to their institution via **ROR**."
)
st.divider()

# ── Model loader ──────────────────────────────────────────────────────────────
ml_left, ml_center, ml_right = st.columns([1, 3, 1])
with ml_center:
    st.subheader("Model Status")
    status_col, button_col = st.columns([3, 1])
    with status_col:
        if st.session_state.get("models_loaded"):
            st.success("✅ Models loaded and ready (spaCy `en_core_web_sm` + GLiNER `gliner_medium-v2.1`)")
        else:
            st.warning("⚠️ Models not loaded — click **Load Models** before running a search.")
    with button_col:
        if st.button(
            "Load Models",
            type="primary",
            use_container_width=True,
            disabled=bool(st.session_state.get("models_loaded")),
        ):
            with st.status("Loading NLP models …", expanded=True) as ms:
                try:
                    st.write("Loading spaCy `en_core_web_sm` …")
                    st.write("Loading GLiNER `urchade/gliner_medium-v2.1` (downloads on first run) …")
                    nlp, gliner_model = _load_models()
                    st.session_state.nlp = nlp
                    st.session_state.gliner_model = gliner_model
                    st.session_state.models_loaded = True
                    st.write("✅ Models loaded.")
                    ms.update(label="Models loaded", state="complete")
                    st.rerun()
                except Exception as e:
                    ms.update(label=f"Model load failed: {e}", state="error")
                    st.error(f"Failed to load models: {e}")

st.divider()

# ── Input Form ────────────────────────────────────────────────────────────────
form_left, form_center, form_right = st.columns([1, 3, 1])
with form_center:
    with st.form("search_form"):
        col1, col2 = st.columns(2)
        with col1:
            author_name = st.text_input(
                "Author Name *",
                placeholder="e.g., Robert Alexander",
                help="Full name as it appears on publications.",
            )
            country = st.text_input(
                "Country",
                placeholder="e.g., United States",
                help="Used to narrow down institution lookup.",
            )
        with col2:
            affiliation = st.text_input(
                "Affiliation *",
                placeholder="e.g., NY Institute of Technology",
                help="Institution name to resolve to a ROR ID.",
            )
            email = st.text_input(
                "Email",
                placeholder="e.g., ralexa04@nyit.edu",
                help="Institutional email used for domain-based ROR matching.",
            )

        submitted = st.form_submit_button(
            "Run Proactive Search", type="primary", use_container_width=True
        )

# ── Execution ─────────────────────────────────────────────────────────────────
if submitted:
    author_name = author_name.strip()
    affiliation = affiliation.strip()
    country     = country.strip() or None
    email       = email.strip()   or None

    if not author_name:
        st.error("Author Name is required.")
        st.stop()
    if not affiliation:
        st.error("Affiliation is required.")
        st.stop()
    if not st.session_state.get("models_loaded"):
        st.error("Models are not loaded yet. Please click **Load Models** above first.")
        st.stop()

    from affiliation  import evaluate_affiliation_v2
    from pubmed       import get_publications
    from parser_utils import extract_publication_summary, summarize_publications

    nlp          = st.session_state.nlp
    gliner_model = st.session_state.gliner_model

    # ── Step 1 · Resolve institution ──────────────────────────────────────────
    ror_id   = None
    ror_name = affiliation

    with st.status("Step 1 — Resolving institution ROR ID …", expanded=True) as s1:
        try:
            st.write(f"Querying ROR/Hipolabs for **{affiliation}**")
            if country: st.write(f"Country: {country}")
            if email:   st.write(f"Email domain hint: `{email.split('@')[-1]}`")

            org    = evaluate_affiliation_v2({"query": affiliation, "country": country, "email": email})
            result = org.get("ror", {}).get("result", {})
            ror_id   = result.get("ror_id")
            ror_name = result.get("ror_names") or affiliation

            if ror_id:
                st.write(f"✅ ROR ID: `{ror_id}` — **{ror_name}**")
                s1.update(label=f"Step 1 — Resolved: {ror_name}", state="complete")
            else:
                st.write("⚠️ ROR ID not found — results will not be filtered by institution.")
                s1.update(label="Step 1 — ROR ID not resolved", state="error")
        except Exception as e:
            st.error(f"Error resolving institution: {e}")
            s1.update(label="Step 1 — Error", state="error")
            st.stop()

    # ── Step 2 · Fetch publications ───────────────────────────────────────────
    raw_pubs = {}

    with st.status("Step 2 — Fetching PubMed publications …", expanded=True) as s2:
        st.write("⚠️ This may take **several minutes** for common author names (fetching back to 2015).")
        fetch_log = st.empty()
        fetch_messages: list[str] = []

        def _fetch_log(msg: str) -> None:
            fetch_messages.append(str(msg))
            tail = fetch_messages[-200:]
            fetch_log.markdown("\n\n".join(tail))

        try:
            raw_pubs = get_publications(
                terms=[f"{author_name}[Author]"],
                log_callback=_fetch_log,
            )
            s2.update(label=f"Step 2 — Fetched {len(raw_pubs)} publications", state="complete")
        except Exception as e:
            st.error(f"Error fetching publications: {e}")
            s2.update(label="Step 2 — Error", state="error")
            st.stop()

    if not raw_pubs:
        st.warning("No publications found on PubMed for this author.")
        st.stop()

    # ── Step 3 · Process affiliations ─────────────────────────────────────────
    publications = []

    with st.status("Step 3 — Resolving affiliations …", expanded=True) as s3:
        total = len(raw_pubs)
        st.write(f"Processing **{total}** publications (NER + ROR lookup per publication)…")
        prog        = st.progress(0, text=f"0 / {total}")
        counter     = st.empty()
        log_box     = st.empty()
        log_lines: list[str] = []

        def _step_log(msg: str) -> None:
            log_lines.append(str(msg))
            tail = log_lines[-300:]
            log_box.markdown("\n\n".join(tail))

        matched_count = 0
        for i, pub in enumerate(raw_pubs.values(), start=1):
            _step_log(f"**[{i}/{total}]** processing publication")
            try:
                summary = extract_publication_summary(
                    pub=pub,
                    query_author=f"{author_name}[Author]",
                    nlp=nlp,
                    gliner_model=gliner_model,
                    log_callback=_step_log,
                )
                publications.append(summary)
                if summary.get("matched_ror_id") and summary["matched_ror_id"] == ror_id:
                    matched_count += 1
            except Exception as e:
                _step_log(f"   ⚠️ skipped due to error: {e}")

            prog.progress(i / total, text=f"{i} / {total}")
            counter.markdown(
                f"Processed **{i}/{total}** · matched to target ROR: **{matched_count}**"
            )

        s3.update(label=f"Step 3 — Processed {len(publications)} publications", state="complete")

    # ── Step 5 · Filter & summarise ───────────────────────────────────────────
    if ror_id:
        lead_pubs = [p for p in publications if p.get("matched_ror_id") == ror_id]
    else:
        lead_pubs = publications

    summary = summarize_publications(lead_pubs)

    # ── Results ───────────────────────────────────────────────────────────────
    st.divider()
    st.header("Results")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Publications", len(publications))
    c2.metric("Matched to Institution", len(lead_pubs))
    c3.metric("Unique Keywords", len(summary.get("keyword", {})))
    c4.metric("Unique MeSH Terms", len(summary.get("mesh", {})))

    if not lead_pubs:
        st.warning("No publications matched the resolved institution ROR ID.")
        st.stop()

    # ── Publications table ────────────────────────────────────────────────────
    st.subheader("Lead Publications")

    rows = []
    for p in lead_pubs:
        aff_text = ""
        if p.get("matched_affiliations"):
            aff_text = p["matched_affiliations"][0].get("affiliation") or ""
        if len(aff_text) > 120:
            aff_text = aff_text[:117] + "…"

        pmid = p.get("pmid", "")
        rows.append({
            "PMID":                pmid,
            "PubMed Link":         f"https://pubmed.ncbi.nlm.nih.gov/{pmid}" if pmid else "",
            "Title":               p.get("title", ""),
            "Date":                p.get("date", ""),
            "Matched Affiliation": aff_text,
            "Keywords":            ", ".join((p.get("keywords") or [])[:5]),
            "MeSH Terms":          ", ".join((p.get("mesh_terms") or [])[:5]),
        })

    df = pd.DataFrame(rows)
    st.dataframe(
        df,
        use_container_width=True,
        column_config={
            "PubMed Link": st.column_config.LinkColumn("PubMed Link"),
        },
    )

    csv_data = df.drop(columns=["PubMed Link"]).to_csv(index=False)
    st.download_button(
        label="📥 Download as CSV",
        data=csv_data,
        file_name=f"proactive_search_{author_name.replace(' ', '_')}.csv",
        mime="text/csv",
    )

    # ── Charts ────────────────────────────────────────────────────────────────
    col_kw, col_mesh = st.columns(2)

    with col_kw:
        if summary.get("keyword"):
            st.subheader("Top Keywords")
            kw_items = sorted(summary["keyword"].items(), key=lambda x: -x[1])[:15]
            st.bar_chart(
                pd.DataFrame(kw_items, columns=["Keyword", "Count"]).set_index("Keyword"),
                height=350,
            )

    with col_mesh:
        if summary.get("mesh"):
            st.subheader("Top MeSH Terms")
            mesh_items = sorted(summary["mesh"].items(), key=lambda x: -x[1])[:15]
            st.bar_chart(
                pd.DataFrame(mesh_items, columns=["MeSH Term", "Count"]).set_index("MeSH Term"),
                height=350,
            )

    # ── Full lead insights ────────────────────────────────────────────────────
    with st.expander("Full Lead Insights (JSON)"):
        st.json({
            "query":   affiliation,
            "country": country,
            "email":   email,
            "ror_id":  ror_id,
            "publications": {
                "number_of_publications": summary["number_of_publications"],
                "mesh":    summary["mesh"],
                "keyword": summary["keyword"],
            },
        })
