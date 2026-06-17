import streamlit as st
import pandas as pd

st.set_page_config(
    page_title="Proactive Search",
    page_icon="🔍",
    layout="wide",
)


@st.cache_resource(show_spinner="Loading NLP models (first run may take a moment)...")
def _load_models():
    from parser_utils import load_models
    return load_models()


# ── Header ────────────────────────────────────────────────────────────────────
st.title("🔍 Proactive Search")
st.markdown(
    "Search PubMed for an author's publications and surface those linked to their institution via **ROR**."
)
st.divider()

# ── Input Form ────────────────────────────────────────────────────────────────
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

    from affiliation  import evaluate_affiliation_v2
    from pubmed       import get_publications
    from parser_utils import extract_publication_summary, summarize_publications

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

    # ── Step 2 · Load NLP models ──────────────────────────────────────────────
    nlp, gliner_model = _load_models()

    # ── Step 3 · Fetch publications ───────────────────────────────────────────
    raw_pubs = {}

    with st.status("Step 2 — Fetching PubMed publications …", expanded=True) as s2:
        st.write("⚠️ This may take **several minutes** for common author names (fetching back to 2015).")
        try:
            raw_pubs = get_publications(
                terms=[f"{author_name}[Author]"],
                log_callback=st.write,
            )
            s2.update(label=f"Step 2 — Fetched {len(raw_pubs)} publications", state="complete")
        except Exception as e:
            st.error(f"Error fetching publications: {e}")
            s2.update(label="Step 2 — Error", state="error")
            st.stop()

    if not raw_pubs:
        st.warning("No publications found on PubMed for this author.")
        st.stop()

    # ── Step 4 · Process affiliations ─────────────────────────────────────────
    publications = []

    with st.status("Step 3 — Resolving affiliations …", expanded=True) as s3:
        st.write(f"Processing {len(raw_pubs)} publications (this may also take a while)…")
        prog  = st.progress(0)
        total = len(raw_pubs)

        for i, pub in enumerate(raw_pubs.values()):
            try:
                publications.append(
                    extract_publication_summary(
                        pub=pub,
                        query_author=f"{author_name}[Author]",
                        nlp=nlp,
                        gliner_model=gliner_model,
                    )
                )
            except Exception:
                pass
            prog.progress((i + 1) / total)

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
