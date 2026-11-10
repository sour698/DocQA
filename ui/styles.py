"""Global CSS for the DocQA dashboard (injected once by streamlit_app.py)."""

STYLES = """
<style>

@import url(
'https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap'
);

/* MAIN APPLICATION — natural page scrolling */
html, body, #root {
    min-height: 100% !important;
    margin: 0 !important;
}

.stApp {
    min-height: 100vh !important;
    background: #080d19 !important;
    color: #f1f5ff !important;
    font-family: 'Inter', sans-serif !important;
    overflow: visible !important;
}

[data-testid="stAppViewContainer"] {
    min-height: 100vh !important;
    overflow: visible !important;
}

[data-testid="stMain"] {
    height: auto !important;
    max-height: none !important;
    overflow: visible !important;
}

/* Reserve space for the fixed header only */
[data-testid="stMainBlockContainer"],
[data-testid="stAppViewBlockContainer"] {
    max-width: 100% !important;
    min-height: 100vh !important;
    box-sizing: border-box !important;
    padding: 142px 1.2rem 30px !important;
    overflow: visible !important;
}

/* Hide Streamlit's top chrome so it cannot overlap the DocQA header */
header[data-testid="stHeader"],
[data-testid="stToolbar"],
[data-testid="stDecoration"],
#MainMenu {
    display: none !important;
}

/* Fixed DocQA header */
div[data-testid="stElementContainer"]:has(.top-header) {
    position: fixed !important;
    top: 0 !important;
    left: 0 !important;
    right: 0 !important;
    width: 100% !important;
    z-index: 10000 !important;
    box-sizing: border-box !important;
    padding: 16px max(1.2rem, calc((100vw - 1700px) / 2)) 10px !important;
    margin: 0 !important;
    background: rgba(8, 13, 25, 0.98) !important;
    backdrop-filter: blur(16px);
}

/* Natural browser scrollbar */
html {
    overflow-y: auto !important;
    scrollbar-gutter: stable;
}
html::-webkit-scrollbar { width: 9px; }
html::-webkit-scrollbar-track { background: #080d19; }
html::-webkit-scrollbar-thumb {
    background: #475569;
    border-radius: 10px;
    border: 2px solid #080d19;
}
html::-webkit-scrollbar-thumb:hover { background: #8068ff; }

/* REMOVE STREAMLIT DECORATIONS */

#MainMenu {
    visibility: hidden;
}

footer {
    visibility: hidden;
}

/* HEADER */

.top-header {
    width: 100%;
    box-sizing: border-box;

    display: flex;
    align-items: center;
    gap: 15px;

    padding: 17px 22px;
    margin-bottom: 0;

    background: rgba(16, 23, 40, 0.98);
    border: 1px solid #354765;
    border-radius: 15px;
}

.brand-icon {
    width: 54px;
    height: 54px;
    min-width: 54px;

    display: flex;
    align-items: center;
    justify-content: center;

    border-radius: 15px;
    background: linear-gradient(135deg, #8264ff, #5635df);

    color: white;
    font-size: 30px;
    font-weight: 800;
}

.brand-info {
    display: flex;
    flex-direction: column;
}

.brand-name {
    color: white;
    font-size: 26px;
    font-weight: 800;
    line-height: 1.3;
}

.brand-sub {
    color: #a5b4cc;
    font-size: 12px;
    margin-top: 4px;
}

.brand-tag {
    background: #29264c;
    color: #b5a7ff;
    border: 1px solid #5144a5;
    border-radius: 22px;
    padding: 9px 14px;
    font-size: 10px;
    font-weight: 800;
    white-space: nowrap;
}

.header-spacer {
    flex: 1;
}

.system-status {
    background: #102b2b;
    color: #10d9a0;
    border: 1px solid #155b53;
    border-radius: 22px;
    padding: 10px 14px;
    font-size: 10px;
    font-weight: 700;
    white-space: nowrap;
}

.system-status.offline {
    background: #2b1010;
    color: #f87171;
    border-color: #5b1f1f;
}

.ai-avatar {
    width: 46px;
    height: 46px;
    min-width: 46px;
    border-radius: 50%;

    background: linear-gradient(135deg, #7771ff, #5142e7);

    display: flex;
    align-items: center;
    justify-content: center;

    color: white;
    font-weight: 800;
}

/* PANEL STYLING */

[data-testid="stVerticalBlockBorderWrapper"] {
    background: #101829;
    border: 1px solid #293750;
    border-radius: 14px;
    padding: 14px;
}

.panel-heading {
    display: flex;
    align-items: center;
    gap: 10px;

    padding-bottom: 14px;
    margin-bottom: 16px;

    border-bottom: 1px solid #293750;

    color: #f1f5ff;
    font-size: 16px;
    font-weight: 800;
}

.panel-icon {
    color: #a095ff;
    font-size: 21px;
}

.section-label {
    color: #8295b5;
    font-size: 10px;
    font-weight: 800;
    letter-spacing: 1.3px;
    margin: 20px 0 12px;
}

/* ANSWER SOURCE BADGES */

.badge {
    display: inline-block;
    padding: 3px 11px;
    border-radius: 999px;
    font-size: 11px;
    font-weight: 700;
    margin-bottom: 8px;
}

.badge-doc {
    background: #10302a;
    color: #6ee7b7;
    border: 1px solid #1f6b52;
}

.badge-gen {
    background: #3a2a10;
    color: #fbbf24;
    border: 1px solid #7a5a1a;
}

/* DOCUMENT CARDS */

.doc-card {
    background: #172237;
    border: 1px solid #2a3a55;
    border-radius: 10px;
    padding: 13px;
    margin: 10px 0;
    overflow-wrap: anywhere;
}

.doc-card.selected {
    background: #25254b;
    border-color: #7768ff;
}

.doc-name {
    color: #f1f5ff;
    font-size: 12px;
    font-weight: 700;
}

.doc-description {
    color: #91a6c7;
    font-size: 10px;
    margin-top: 7px;
}

/* FILE UPLOADER */

[data-testid="stFileUploader"] {
    background: #172237;
    border: 1px dashed #695ce1;
    border-radius: 11px;
    padding: 10px;
}

/* BUTTONS */

.stButton > button {
    width: 100%;
    min-height: 39px;

    background: linear-gradient(110deg, #6355ed, #8155ed);
    border: 1px solid #8275ff;
    border-radius: 9px;

    color: white;
    font-size: 12px;
    font-weight: 600;
}

.stButton > button:hover {
    background: linear-gradient(110deg, #5142df, #7139db);
    border-color: #a49aff;
    color: white;
}

/* VIEWER */

.viewer-toolbar {
    background: #111b2c;
    border: 1px solid #293750;
    border-radius: 9px;
    padding: 13px;
    margin-bottom: 12px;

    color: #dce6ff;
    font-size: 12px;
    font-weight: 600;
    overflow-wrap: anywhere;
}

.viewer-empty {
    width: 100%;
    min-height: 380px;
    box-sizing: border-box;

    background: #151f33;
    border: 1px dashed #465779;
    border-radius: 14px;

    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;

    text-align: center;
    padding: 25px 15px;
}

.viewer-icon {
    font-size: 60px;
    margin-bottom: 15px;
}

.viewer-title {
    color: white;
    font-size: 19px;
    font-weight: 800;
}

.viewer-description {
    color: #9aabc7;
    font-size: 12px;
    line-height: 1.9;
    margin-top: 12px;
}

/* AI ASSISTANT */

.assistant-card {
    background: linear-gradient(145deg, #192640, #172237);
    border: 1px solid #354765;
    border-radius: 13px;
    padding: 15px;
}

.assistant-header {
    display: flex;
    align-items: center;
    gap: 12px;
}

.assistant-avatar {
    width: 47px;
    height: 47px;
    min-width: 47px;
    border-radius: 50%;

    background: linear-gradient(135deg, #8264ff, #5635df);

    display: flex;
    align-items: center;
    justify-content: center;

    color: white;
    font-size: 16px;
    font-weight: 800;
}

.assistant-title {
    color: white;
    font-size: 14px;
    font-weight: 800;
}

.assistant-subtitle {
    color: #aab8d0;
    font-size: 10px;
    margin-top: 4px;
}

.assistant-message {
    background: #202e47;
    border: 1px solid #354765;
    border-radius: 10px;
    padding: 13px;
    margin-top: 15px;

    color: #e5edff;
    font-size: 12px;
    line-height: 1.8;
}

/* CHAT */

[data-testid="stChatMessage"] {
    background: #172237;
    border: 1px solid #30415e;
    border-radius: 12px;
    padding: 10px;
    margin-bottom: 9px;
    overflow-wrap: anywhere;
}

/* Chat composer: a normal form that sits in the panel flow */
[data-testid="stForm"] {
    margin-top: 14px;
    padding: 12px;
    background: #111b2c;
    border: 1px solid #354765;
    border-radius: 12px;
}

[data-testid="stTextInput"] input {
    background: #172237 !important;
    color: #f1f5ff !important;
    border: 1px solid #465779 !important;
    border-radius: 10px !important;
}

[data-testid="stFormSubmitButton"] button {
    min-height: 42px;
}

/* CITATIONS */

[data-testid="stExpander"] {
    background: #172237;
    border: 1px solid #30415e;
    border-radius: 9px;
    margin-bottom: 8px;
}

/* FOOTER — normal flow, never covers the page */

.footer {
    width: 100%;
    box-sizing: border-box;
    color: #91a1bb;
    text-align: center;
    font-size: 11px;
    padding: 22px 20px 8px;
    margin: 18px 0 0;
    border-top: 1px solid #26344b;
}

/* RESPONSIVE HEADER */

@media(max-width: 850px) {
    .brand-tag,
    .system-status {
        display: none;
    }

    .brand-name {
        font-size: 21px;
    }

    .top-header {
        gap: 10px;
        padding: 13px;
    }

    .brand-icon {
        width: 44px;
        height: 44px;
        min-width: 44px;
    }

    .ai-avatar {
        width: 40px;
        height: 40px;
        min-width: 40px;
    }
}


@media (max-width: 850px) {
    [data-testid="stMainBlockContainer"],
    [data-testid="stAppViewBlockContainer"] {
        padding: 120px 0.65rem 30px !important;
    }
    div[data-testid="stElementContainer"]:has(.top-header) {
        padding: 10px 0.65rem 8px !important;
    }
}
/* MULTIMODAL RAG ASSISTANT — BUTTON AND CONTENT FIX */

[data-testid="column"] {
    min-width: 0 !important;
}

[data-testid="stButton"] {
    width: 100% !important;
    min-width: 0 !important;
}

[data-testid="stButton"] > button {
    display: flex !important;
    width: 100% !important;
    min-width: 100% !important;
    justify-content: center !important;
    align-items: center !important;
    white-space: normal !important;
    height: auto !important;
    min-height: 42px !important;
    padding: 10px 12px !important;
}

[data-testid="stForm"] {
    width: 100% !important;
    box-sizing: border-box !important;
}

[data-testid="stTextInput"] {
    width: 100% !important;
}

/* Stack dashboard panels on narrow screens */
@media (max-width: 850px) {
    [data-testid="stHorizontalBlock"] {
        flex-wrap: wrap !important;
    }

    [data-testid="column"] {
        width: 100% !important;
        min-width: 100% !important;
        flex: 1 1 100% !important;
    }
}

/* FEEDBACK BUTTONS */

.feedback-heading {
    color: #aab8d0;
    font-size: 11px;
    font-weight: 600;
    margin-top: 12px;
    margin-bottom: 8px;
}

[data-testid="stChatMessage"] [data-testid="stButton"] {
    width: 100% !important;
}

[data-testid="stChatMessage"] [data-testid="stButton"] button {
    width: 100% !important;
    min-width: 0 !important;
    min-height: 42px !important;
    height: auto !important;
    padding: 9px 12px !important;
    border-radius: 9px !important;
    font-size: 12px !important;
    font-weight: 600 !important;
    white-space: nowrap !important;
    justify-content: center !important;
    color: #ffffff !important;
}

</style>
"""
