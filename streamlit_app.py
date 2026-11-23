import ssl

# Fix for "CERTIFICATE_VERIFY_FAILED: Missing Authority Key Identifier" (Python 3.13+ with antivirus/proxy HTTPS scanning).
# Certificates and host names are still verified; only the extra-strict X.509 check is relaxed.
_ssl_ctx = ssl.create_default_context()
_ssl_ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
ssl._create_default_https_context = lambda *args, **kwargs: _ssl_ctx
import ssl

# Fix for "CERTIFICATE_VERIFY_FAILED: Missing Authority Key Identifier" (Python 3.13+ with antivirus/proxy HTTPS scanning).
# Certificates and host names are still verified; only the extra-strict X.509 check is relaxed.
_ssl_ctx = ssl.create_default_context()
_ssl_ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
ssl._create_default_https_context = lambda *args, **kwargs: _ssl_ctx

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from html import escape

from auth import require_login
from handwriting import read_handwritten_prescription  # <-- HANDWRITING ADDED (1 of 2)

from med_logic import (
    INTERACTIONS,
    KNOWN_DRUGS,
    MEDICINE_DB,
    SYMPTOMS,
    assess_blood_pressure,
    assess_heart_rate,
    check_interactions,
    extract_medicines_from_prescription,
    get_medicine_options,
    get_side_effects,
    get_symptom_guidance,
    ai_error_message,
    ai_interaction_analysis,
    get_ai_key,
    label_to_name,
    online_interaction_check,
    online_name_suggestions,
    suggest_medicines,
)

try:
    from i18n import LANGS, current_lang, t  # English / Hindi / Marathi texts
    from translate import localize_vital, tr_details, tr_guidance, tr_list, tr_notice  # Hindi / Marathi medical text
except Exception as _import_error:  # a project file is missing or is an old version
    st.set_page_config(page_title="MedSafe AI", page_icon="\U0001F48A")
    st.error(f"A MedSafe AI file is missing or outdated: {_import_error}")
    st.info("Copy ALL the files from MedSafe_AI_Project.zip into your project folder (replace the old ones), "
            "then close this window and start the app again. Run  python check_setup.py  to see which file is wrong.")
    st.stop()
from ui_helpers import DEFAULT_THEME, THEMES, theme_css, DIA_ZONES, HR_ZONES, SYS_ZONES, build_pdf_report, gauge_html, interaction_map, timetable_html  # GUI helpers: map, timetable, PDF

# 1. Page configuration (must be the first Streamlit call)
st.set_page_config(page_title="MedSafe AI", page_icon="💊", layout="wide")
st.session_state.setdefault("lang", "en")  # language: en / hi / mr


# Language buttons at the very top of every page (also on the login page): English | हिन्दी | मराठी
_cols = st.columns([5, 1, 1, 1])
for _col, _code in zip(_cols[1:], LANGS):
    if _col.button(LANGS[_code], key=f"lang_{_code}", type="primary" if st.session_state["lang"] == _code else "secondary"):
        st.session_state["lang"] = _code
        st.rerun()

st.markdown(
    """
    <style>
    /* Pushes main container down to prevent floating Streamlit banners from overlapping header elements */
    .block-container, [data-testid="stMainBlockContainer"] {padding-top: 4.5rem !important;}
    
    .ms-banner {background: linear-gradient(120deg,#0B2545,#13607a); color:#fff; padding:18px 26px; border-radius:14px; margin-bottom:8px;}
    .ms-banner .t {font-size:1.9rem; font-weight:700; margin:0;}
    .ms-banner .s {margin:4px 0 0; color:#CFE3F3; font-size:0.95rem;}
    .ms-card {background:#fff; border:1px solid #d6e2ee; border-radius:10px; padding:10px 14px; margin:8px 0;
              box-shadow:0 1px 4px rgba(11,37,69,.08); color:#1A2433;}
    button[data-baseweb="tab"] {font-size:1rem; font-weight:600;}
    div.stButton > button[kind="primary"], div.stDownloadButton > button {border-radius:10px; font-weight:600;}
    </style>
    <div class="ms-banner"><div class="t">🛡️ MedSafe AI</div>
    <div class="s">__SUB__</div></div>
    """.replace("__SUB__", t("banner_sub")),
    unsafe_allow_html=True,
)
_theme_css = theme_css(st.session_state.get("theme", DEFAULT_THEME))  # colour theme chosen in the sidebar
try:
    st.html(_theme_css)
except Exception:  # older Streamlit without st.html
    st.markdown(_theme_css, unsafe_allow_html=True)
st.caption(t("disclaimer"))

require_login()  # shows the login page and stops here until the user logs in

# Theme picker (the whole app changes colour; the choice is kept while you use the app)
with st.sidebar:
    st.selectbox(t("theme_label"), list(THEMES), key="theme")

SEVERITY_LABEL = {
    "critical": "🚨 CRITICAL",
    "severe": "🔴 SEVERE",
    "moderate": "🟠 MODERATE",
}


def ai_err(status):
    """AI service problem in the chosen language."""
    if status in ("no_key", "auth", "rate", "offline", "error"):
        return t("ai_err_" + status)
    return ai_error_message(status)


def show_level(level, message):
    """Show a vital-sign result in the right colour."""
    message = localize_vital(message)
    if level == "emergency":
        st.error(f"🚨 {message}")
    elif level == "warning":
        st.warning(f"⚠️ {message}")
    else:
        st.success(f"✅ {message}")


def show_interactions(findings, unrecognized, online=False):
    """Shared display for Tab 1 and Tab 2."""
    if unrecognized:
        st.warning(t("not_in_db", names=", ".join(unrecognized)))
        for name in unrecognized:
            tips = suggest_medicines(name)
            if not tips and online:
                tips = online_name_suggestions(name)  # NLM RxNorm spelling suggestions
            if tips:
                st.info(t("did_you_mean_for", name=name, tips=", ".join(tips)))

    if findings:
        worst = findings[0]["severity"]
        if worst in ("critical", "severe"):
            st.error(t("serious_detected"))
        else:
            st.warning(t("interaction_detected"))
        colors = {"critical": "#8B0000", "severe": "#E63946", "moderate": "#F4A261"}
        _msgs = tr_list([f["message"] for f in findings])
        for f, _msg in zip(findings, _msgs):
            col = colors[f["severity"]]
            st.markdown(
                f'<div class="ms-card" style="border-left:6px solid {col};"><b style="color:{col}">{t("sev_" + f["severity"])}</b>'
                f' - <b>{escape(f["drugs"].replace("duplicate ingredient", t("dup_label")))}</b><br>{escape(_msg)}</div>',
                unsafe_allow_html=True,
            )
        st.info(t("talk_doctor"))
        tr_notice()
    elif unrecognized:
        st.warning(
            t("not_confirmed")
        )
    else:
        st.info(
            t("no_interaction")
        )


def show_online_results(results, status, missing):
    """Display the optional online-library (FDA label) results."""
    st.subheader(t("online_lib"))
    if status == "offline":
        st.warning(t("online_unreach"))
        return

    if results:
        st.warning(t("fda_mentions"))
        for r in results:
            st.markdown(t("fda_item", drugs=r["drugs"], source=r["source"], mentioned=r["mentioned"]))
            safe = r['excerpt'].replace('$', '\\$')
            st.caption(f"\u201c{safe}\u201d")
        st.info(t("fda_note"))
    else:
        st.info(t("fda_none"))

    if missing:
        st.caption(t("fda_missing", names=", ".join(missing)))
    st.caption(t("fda_source"))


def show_map(meds, findings):
    """Circles = medicines, lines = interactions (red severe, orange moderate)."""
    html, height = interaction_map(meds, findings)
    if html:
        st.markdown(t("map_title"))
        components.html(html, height=height)


def _tt_labels():
    return {k: t("tt_" + k) for k in ("med", "morning", "afternoon", "night", "notes", "sos", "after", "before", "empty", "nosched")}


def show_timetable(rows):
    """Morning / Afternoon / Night chart built from the dose pattern (e.g. 1-0-1)."""
    st.markdown(t("tt_title"))
    if not any(dose for _, dose in rows):
        st.info(t("tt_none"))
        return
    html, height = timetable_html(rows, _tt_labels())
    components.html(html, height=height)


def show_report_button(key, rows, findings, unrecognized, flags=None):
    """Download a one-page PDF report."""
    try:
        pdf = build_pdf_report(st.session_state.get("user"), rows, findings, unrecognized, flags)
    except ImportError:
        st.caption(t("pdf_need"))
        return
    if current_lang() != "en":
        st.caption(t("pdf_en_note"))
    st.download_button(t("pdf_btn"), data=pdf, file_name="MedSafe_AI_Report.pdf",
                       mime="application/pdf", key=f"dl_{key}")


# 2. Tabs
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    t("tab1"),
    t("tab2"),
    t("tab3"),
    t("tab4"),
    t("tab5"),
])

# --- TAB 1: INTERACTION CHECKER ---
def read_ai_key():
    """The Anthropic key stored in the program (api_keys.py) if any. Gemini is picked up automatically."""
    return get_ai_key()


def split_names(text):
    return [n.strip() for n in text.replace(";", ",").split(",") if n.strip()]


with tab1:
    st.header(t("tab1"))
    st.caption(t("t1_caption"))

    options = get_medicine_options()

    def _opt(prefix):
        """The dropdown label that starts with this name (brands show their ingredients in brackets)."""
        return next((o for o in options if o == prefix or o.startswith(prefix + " (")), prefix)

    st.session_state.setdefault("meds_sel", ["Aspirin", "Warfarin"])

    def _use_example(meds):
        st.session_state["meds_sel"] = [_opt(m) for m in meds]

    st.markdown(t("try_example"))
    _e1, _e2, _e3, _e4 = st.columns(4)
    _e1.button("Aspirin + Warfarin", on_click=_use_example, args=(["Aspirin", "Warfarin"],), key="ex1")
    _e2.button("Metformin + Insulin", on_click=_use_example, args=(["Metformin", "Insulin"],), key="ex2")
    _e3.button("Augmentin + Enzoflam + Warfarin", on_click=_use_example, args=(["Augmentin", "Enzoflam", "Warfarin"],), key="ex3")
    _e4.button(t("ex4"), on_click=_use_example, args=(["Crocin", "Enzoflam"],), key="ex4")

    ms_args = dict(
        label=t("pick_meds"),
        options=options,
        key="meds_sel",
        placeholder=t("type_med"),
    )
    try:
        # Newer Streamlit: lets you also add a name that isn't in the list
        selected = st.multiselect(**ms_args, accept_new_options=True)
    except TypeError:
        selected = st.multiselect(**ms_args)

    extra_text = st.text_input(
        t("extra_meds"),
        placeholder="e.g. Rifampin, Montair, Pantop D",
        key="extra_meds",
    )

    colA, colB = st.columns(2)
    use_online = colA.checkbox(
        t("chk_online"),
        value=True,
        help=t("chk_online_help"),
    )
    use_ai = colB.checkbox(
        t("chk_ai"),
        value=True,
        help=t("chk_ai_help"),
    )

    if st.button(t("btn_check"), type="primary"):
        med_list, seen = [], set()
        for name in [label_to_name(x) for x in selected] + split_names(extra_text):
            if name.lower() not in seen:
                seen.add(name.lower())
                med_list.append(name)

        if len(med_list) < 2:
            st.session_state.pop("t1", None)
            st.warning(t("need_two"))
        else:
            findings, unrecognized = check_interactions(med_list)
            res = {"meds": med_list, "findings": findings, "unrecognized": unrecognized,
                   "use_online": use_online, "online": None, "ai": None}
            if use_online:
                with st.spinner(t("spin_online")):
                    res["online"] = online_interaction_check(med_list)
            if use_ai:
                with st.spinner(t("spin_ai")):
                    res["ai"] = ai_interaction_analysis(
                        med_list, findings, res["online"][0] if res["online"] else [], api_key=read_ai_key(), lang=current_lang()
                    )
            st.session_state["t1"] = res

    t1 = st.session_state.get("t1")
    if t1:
        st.markdown(t("checking") + ", ".join(t1["meds"]))
        show_interactions(t1["findings"], t1["unrecognized"], online=t1["use_online"])
        show_map(t1["meds"], t1["findings"])
        if t1["online"] is not None:
            st.divider()
            show_online_results(*t1["online"])
        if t1["ai"] is not None:
            st.divider()
            st.subheader(t("ai_title"))
            ai_text, ai_status = t1["ai"]
            if ai_status == "ok":
                st.markdown(ai_text)
                st.caption(t("ai_note"))
            elif ai_status == "no_key":
                st.info(t("ai_off"))
            else:
                st.warning(ai_err(ai_status))
        st.divider()
        show_report_button("t1", [(m, "") for m in t1["meds"]], t1["findings"], t1["unrecognized"])

    with st.expander(t("known_exp")):
        st.write(", ".join(sorted(d.title() for d in KNOWN_DRUGS)))
        st.caption(t("known_cap"))

# --- TAB 2: PRESCRIPTION OCR ---
def store_rx(meds, raw_text):
    """Keep the detected medicines so the editable table survives page reruns."""
    ver = st.session_state.get("rx", {}).get("ver", 0) + 1
    st.session_state["rx"] = {"meds": meds, "raw": raw_text, "ver": ver}


with tab2:
    st.header(t("t2_header"))
    st.caption(t("t2_caption"))

    source = st.radio(t("photo_source"), ["up", "cam"], format_func=lambda v: t("src_upload") if v == "up" else t("src_camera"),
                      horizontal=True, key="rx_source")
    if source == "cam":
        uploaded_file = st.camera_input(t("cam_label"))
    else:
        uploaded_file = st.file_uploader(t("upl_label"), type=["jpg", "jpeg", "png"])

    if uploaded_file:
        col1, col2 = st.columns(2)

        with col1:
            st.image(uploaded_file, caption=t("rx_photo_cap"), use_container_width=True)

        with col2:
            read_mode = st.radio(
                t("mode_q"),
                ["print", "hand"],
                format_func=lambda v: t("mode_print") if v == "print" else t("mode_hand"),
                key="ocr_mode",
            )
            handwritten = read_mode == "hand"
            if handwritten:
                st.caption(t("chk_ai_help"))

            if st.button(t("btn_extract"), type="primary"):
                if handwritten:
                    with st.spinner(t("spin_hand")):
                        hw_meds, raw_text, hw_status = read_handwritten_prescription(
                            uploaded_file.getvalue(), api_key=read_ai_key()
                        )
                    if hw_status == "no_key":
                        st.info(t("hand_nokey"))
                    elif hw_status == "bad_image":
                        st.error(t("bad_image"))
                    elif hw_status != "ok":
                        st.warning(ai_err(hw_status))
                    elif not hw_meds:
                        st.warning(t("none_read"))
                        st.info(t("tips"))
                    else:
                        store_rx([{"Use": True, "Medicine": m["name"], "Dose / schedule": m["dose"],
                                   "Check": "" if m["certain"] else "❓ unclear"} for m in hw_meds], raw_text)
                        st.success(t("detected_n", n=len(hw_meds)))
                        st.caption(t("hand_warn"))
                else:
                    with st.spinner(t("spin_read")):
                        detected, raw_text = extract_medicines_from_prescription(uploaded_file)
                    if detected:
                        store_rx([{"Use": True, "Medicine": m, "Dose / schedule": "", "Check": ""} for m in detected], raw_text)
                        st.success(t("detected_n", n=len(detected)))
                        st.caption(t("ocr_warn"))
                    else:
                        if raw_text.startswith("OCR Error"):
                            st.error(raw_text)
                        else:
                            st.warning(t("no_match"))
                        st.info(t("tips"))
                        st.info(t("choose_hand"))
                        if raw_text:
                            with st.expander(t("view_raw")):
                                st.text(raw_text)

    rx = st.session_state.get("rx")
    if rx:
        st.divider()
        st.subheader(t("edit_title"))
        st.caption(t("edit_cap"))
        df = pd.DataFrame(rx["meds"], columns=["Use", "Medicine", "Dose / schedule", "Check"])
        edited = st.data_editor(
            df, key=f"rx_editor_{rx['ver']}", num_rows="dynamic", hide_index=True, use_container_width=True,
            disabled=["Check"],
            column_config={
                "Use": st.column_config.CheckboxColumn(t("col_use"), default=True, width="small"),
                "Medicine": st.column_config.TextColumn(t("tt_med")),
                "Dose / schedule": st.column_config.TextColumn(t("col_dose"), help=t("col_dose_help")),
                "Check": st.column_config.TextColumn(t("col_check"), width="small"),
            },
        )

        rows, flags, seen_rx = [], {}, set()
        for _, r in edited.iterrows():
            name = "" if pd.isna(r["Medicine"]) else str(r["Medicine"]).strip()
            use = True if pd.isna(r["Use"]) else bool(r["Use"])
            if not name or not use or name.lower() in seen_rx:
                continue
            seen_rx.add(name.lower())
            dose = "" if pd.isna(r["Dose / schedule"]) else str(r["Dose / schedule"]).strip()
            rows.append((name, dose))
            if isinstance(r["Check"], str) and r["Check"]:
                flags[name] = "unclear"

        if rows:
            names = [n for n, _ in rows]
            findings, unrecognized = [], []
            if len(names) >= 2:
                st.subheader(t("inter_check"))
                findings, unrecognized = check_interactions(names)
                show_interactions(findings, unrecognized)
                show_map(names, findings)
            else:
                st.info(t("only_one"))
            show_timetable(rows)
            st.divider()
            show_report_button("rx", rows, findings, unrecognized, flags)
        else:
            st.info(t("tick_one"))

        if rx.get("raw"):
            with st.expander(t("view_text")):
                st.text(rx["raw"])

# --- TAB 3: SYMPTOM GUIDE ---
with tab3:
    st.header(t("t3_header"))
    st.caption(t("t3_caption"))

    user_query = st.text_input(
        t("symp_q"),
        key="symp_in",
        value="headache",
    )

    if st.button(t("btn_symp"), type="primary"):
        if user_query.strip():
            guidance = tr_guidance(get_symptom_guidance(user_query))
            st.subheader(f"📋 {guidance['title']}")

            c1, c2 = st.columns(2)
            with c1:
                st.markdown(t("self_care"))
                for step in guidance["home_care"]:
                    st.write(f"• {step}")
            with c2:
                st.markdown(t("red_flags"))
                for flag in guidance["red_flags"]:
                    st.write(f"⚠️ {flag}")

            st.divider()
            st.info(t("next_steps", text=guidance["recommendation"]))
            tr_notice()
        else:
            st.warning(t("enter_symp"))

# --- TAB 4: SIDE EFFECTS & USE ---
with tab4:
    st.header(t("tab4"))
    st.caption(t("t4_caption"))

    options4 = get_medicine_options()
    sb_args = dict(
        label=t("search_med"),
        options=options4,
        index=options4.index("Ibuprofen"),
        key="side_in",
        placeholder=t("type_med"),
    )
    try:
        choice = st.selectbox(**sb_args, accept_new_options=True)
    except TypeError:
        choice = st.selectbox(**sb_args)
    med_input = label_to_name(choice) if choice else ""

    if st.button(t("btn_med"), type="primary"):
        if med_input.strip():
            details = tr_details(get_side_effects(med_input))
            st.subheader(f"💊 {details['name']}")
            if details.get("auto"):
                st.caption(t("auto_note"))

            st.info(t("used_for", text=details["uses"]))
            if not details["found"]:
                st.caption(t("not_in_db2"))
                tips = suggest_medicines(med_input)
                if tips:
                    st.info(t("did_you_mean", tips=", ".join(tips)))

            c1, c2 = st.columns(2)
            with c1:
                st.markdown(t("common_se"))
                for e in details["common"]:
                    st.write(f"• {e}")
            with c2:
                st.markdown(t("serious_se"))
                for e in details["serious"]:
                    st.write(f"⚠️ {e}")

            st.divider()
            st.info(t("precautions", text=details["precautions"]))
            tr_notice()
        else:
            st.warning(t("enter_med"))

# --- TAB 5: EMERGENCY RISK PREDICTOR (BP + HEART RATE) ---
with tab5:
    st.header(t("tab5"))
    st.caption(t("t5_caption"))
    st.markdown(
        '<div style="background:#B3202A;color:#fff;padding:14px 20px;border-radius:12px;margin-bottom:14px">'
        f'<b style="font-size:1.15rem">{t("em_title")}</b><br>'
        f'{t("em_body")}</div>',
        unsafe_allow_html=True,
    )

    hr = st.slider(t("hr_label"), 30, 200, 75)
    c1, c2 = st.columns(2)
    sys_bp = c1.number_input(t("sys_label"), 60, 260, 120)
    dia_bp = c2.number_input(t("dia_label"), 30, 160, 80)

    g1, g2, g3 = st.columns(3)
    g1.markdown(gauge_html(t("g_hr"), int(hr), HR_ZONES, "BPM"), unsafe_allow_html=True)
    g2.markdown(gauge_html(t("g_sys"), int(sys_bp), SYS_ZONES, "mmHg"), unsafe_allow_html=True)
    g3.markdown(gauge_html(t("g_dia"), int(dia_bp), DIA_ZONES, "mmHg"), unsafe_allow_html=True)
    st.caption(t("g_note"))

    if st.button(t("btn_risk"), type="primary"):
        st.subheader(t("bp_title"))
        show_level(*assess_blood_pressure(int(sys_bp), int(dia_bp)))
        st.subheader(t("g_hr"))
        show_level(*assess_heart_rate(int(hr)))
        st.caption(t("ranges_note"))

st.divider()
st.caption(t("footer_note"))