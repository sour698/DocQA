"""Stylesheet and static markup for the sign-in / create-account screen.

Self-contained on purpose: the dashboard stylesheet is only injected AFTER sign-in, so this one
carries its own font, background and component styling, using the dashboard's colour palette.
"""

AUTH_STYLES = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

/* ---- page ------------------------------------------------------------------------------ */
html, body, .stApp {
    font-family: 'Inter', -apple-system, 'Segoe UI', sans-serif !important;
    color: #f1f5ff !important;
}
.stApp {
    background:
        radial-gradient(900px 520px at 12% -10%, rgba(130, 100, 255, 0.20), transparent 60%),
        radial-gradient(700px 480px at 100% 110%, rgba(16, 217, 160, 0.10), transparent 60%),
        #080d19 !important;
}
header[data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stDecoration"], #MainMenu {
    display: none !important;      /* no Deploy button / menu on the sign-in screen */
}
[data-testid="stMainBlockContainer"], [data-testid="stAppViewBlockContainer"] {
    max-width: 1060px !important;
    padding: 9vh 1.5rem 3rem !important;
}

/* one typeface everywhere on this screen (Streamlit's own widgets default to Source Sans) */
[data-testid="stTab"], [data-testid="stTab"] *, [data-testid="stTextInput"] input,
[data-testid="stForm"] label p, [data-testid="stFormSubmitButton"] button,
[data-testid="stFormSubmitButton"] button *, [data-testid="stAlert"], [data-testid="stAlert"] *,
[data-testid="stSpinner"], [data-baseweb="tab"], [data-baseweb="tab"] * {
    font-family: 'Inter', -apple-system, 'Segoe UI', sans-serif !important;
}

/* ---- brand panel (left) -------------------------------------------------------------- */
.auth-brand { padding: 18px 12px 8px 4px; }
.auth-logo {
    display: flex; align-items: center; gap: 14px; margin-bottom: 34px;
}
.auth-logo-mark {
    width: 52px; height: 52px; border-radius: 15px;
    display: flex; align-items: center; justify-content: center;
    background: linear-gradient(135deg, #8264ff, #5635df);
    box-shadow: 0 10px 30px rgba(86, 53, 223, 0.45);
    color: #fff; font-size: 28px; font-weight: 800;
}
.auth-logo-name { font-size: 26px; font-weight: 800; color: #fff; line-height: 1.1; }
.auth-logo-sub  { font-size: 12px; color: #a5b4cc; margin-top: 4px; letter-spacing: .02em; }
.auth-headline {
    font-size: 38px; line-height: 1.15; font-weight: 800; color: #fff;
    letter-spacing: -0.02em; margin: 0 0 14px;
}
.auth-headline span {
    background: linear-gradient(90deg, #b5a7ff, #8264ff);
    -webkit-background-clip: text; background-clip: text; color: transparent;
}
.auth-lead { color: #a5b4cc; font-size: 16px; line-height: 1.6; margin: 0 0 30px; max-width: 460px; }
.auth-brand ul.auth-features {
    list-style: none !important; padding: 0 !important; margin: 0 !important;
    display: grid; gap: 16px;
}
.auth-brand ul.auth-features li { display: flex; gap: 14px; align-items: flex-start; margin: 0 !important; padding: 0 !important; }
.auth-feature-icon {
    min-width: 38px; height: 38px; border-radius: 11px;
    display: flex; align-items: center; justify-content: center; font-size: 18px;
    background: #29264c; border: 1px solid #5144a5;
}
.auth-feature-title { color: #fff; font-weight: 600; font-size: 14.5px; }
.auth-feature-text  { color: #8fa0bd; font-size: 13px; line-height: 1.5; margin-top: 2px; }

/* ---- form side (right) --------------------------------------------------------------- */
.auth-card-title { font-size: 26px; font-weight: 800; color: #fff; margin: 4px 0 4px; letter-spacing: -0.01em; }
.auth-card-sub   { color: #a5b4cc; font-size: 14px; margin: 0 0 18px; }

/* tabs: Streamlit >= 1.5x uses [role=tablist] / [data-testid=stTab]; older versions used BaseWeb */
[role="tablist"], [data-baseweb="tab-list"] {
    display: flex; gap: 6px; background: rgba(16, 23, 40, 0.9); padding: 5px;
    border: 1px solid #354765; border-radius: 12px; margin-bottom: 16px;
}
[role="tablist"]::after { display: none !important; }              /* grey baseline under the tabs */
[data-testid="stTab"], button[data-baseweb="tab"] {
    flex: 1; height: 40px; justify-content: center; border-radius: 9px !important;
    background: transparent !important; color: #a5b4cc !important;
    font-weight: 600 !important; font-size: 14px !important; cursor: pointer;
}
[data-testid="stTab"] *, button[data-baseweb="tab"] * { color: inherit !important; }
[data-testid="stTab"] > div:nth-child(2) { display: none !important; }   /* red underline */
[data-testid="stTab"]:hover, button[data-baseweb="tab"]:hover { color: #fff !important; }
[data-testid="stTab"][aria-selected="true"], button[data-baseweb="tab"][aria-selected="true"] {
    background: linear-gradient(135deg, #8264ff, #5635df) !important; color: #fff !important;
}
[data-baseweb="tab-highlight"], [data-baseweb="tab-border"] { display: none !important; }

[data-testid="stForm"] {
    background: rgba(16, 23, 40, 0.96); border: 1px solid #354765 !important;
    border-radius: 16px; padding: 24px 24px 20px !important;
    box-shadow: 0 24px 60px rgba(0, 0, 0, 0.35);
}
[data-testid="stForm"] label p {
    color: #c9d4ea !important; font-size: 13px !important; font-weight: 600 !important;
}

/* text boxes: dark field, light text (the root element differs between Streamlit versions) */
[data-testid="stTextInputRootElement"], [data-testid="stTextInput"] [data-baseweb="input"] {
    background: #0b1222 !important; border: 1px solid #354765 !important;
    border-radius: 10px !important; transition: border-color .15s, box-shadow .15s;
}
[data-testid="stTextInput"] [data-baseweb="base-input"] { background: transparent !important; }
[data-testid="stTextInputRootElement"]:focus-within, [data-testid="stTextInput"] [data-baseweb="input"]:focus-within {
    border-color: #8264ff !important; box-shadow: 0 0 0 3px rgba(130, 100, 255, 0.25) !important;
}
[data-testid="stTextInput"] input {
    background: transparent !important; color: #f1f5ff !important; height: 44px; font-size: 14.5px;
    -webkit-text-fill-color: #f1f5ff;
}
[data-testid="stTextInput"] input::placeholder { color: #5f6f8c !important; -webkit-text-fill-color: #5f6f8c; }
[data-testid="stTextInput"] input:-webkit-autofill {              /* browser/password-manager autofill */
    -webkit-text-fill-color: #f1f5ff; box-shadow: 0 0 0 1000px #0b1222 inset; caret-color: #f1f5ff;
}
[data-testid="stTextInputRootElement"] button { background: transparent !important; color: #a5b4cc !important; }
[data-testid="stTextInputRootElement"] button:hover { color: #fff !important; }

[data-testid="stFormSubmitButton"] button {
    height: 46px; border: 0 !important; border-radius: 11px !important;
    background: linear-gradient(135deg, #8264ff, #5635df) !important;
    color: #fff !important; font-weight: 700 !important; font-size: 15px !important;
    box-shadow: 0 10px 26px rgba(86, 53, 223, 0.40); transition: transform .12s, box-shadow .12s;
}
[data-testid="stFormSubmitButton"] button:hover {
    transform: translateY(-1px); box-shadow: 0 14px 32px rgba(86, 53, 223, 0.55);
}
[data-testid="stFormSubmitButton"] button:active { transform: translateY(0); }
[data-testid="stFormSubmitButton"] button * { color: #fff !important; }

[data-testid="InputInstructions"] { display: none !important; }
[data-testid="stAlert"] { border-radius: 12px; }

.auth-hint   { color: #8fa0bd; font-size: 12.5px; margin: -4px 0 6px; line-height: 1.5; }
.auth-footer { color: #7d8dab; font-size: 12.5px; line-height: 1.6; margin-top: 14px; text-align: center; }
.auth-footer b { color: #a5b4cc; font-weight: 600; }
.auth-secure {
    display: flex; justify-content: center; gap: 8px; align-items: center;
    color: #10d9a0; font-size: 12px; font-weight: 600; margin-top: 18px;
}

/* ---- small screens: the form must be visible without scrolling ------------------------- */
@media (max-width: 900px) {
    [data-testid="stMainBlockContainer"], [data-testid="stAppViewBlockContainer"] {
        padding-top: 3vh !important;
    }
    .auth-brand { padding: 0 0 4px; }
    .auth-logo { margin-bottom: 14px; }
    .auth-headline { font-size: 24px; margin-bottom: 0; }
    .auth-brand .auth-lead, .auth-brand ul.auth-features { display: none !important; }
    .auth-card-title { font-size: 22px; }
}
</style>
"""

BRAND_PANEL = """
<div class="auth-brand">
  <div class="auth-logo">
    <div class="auth-logo-mark">◈</div>
    <div>
      <div class="auth-logo-name">DocQA</div>
      <div class="auth-logo-sub">Document question answering</div>
    </div>
  </div>
  <h1 class="auth-headline">Ask your documents.<br><span>Get answers you can verify.</span></h1>
  <p class="auth-lead">
    Upload your files and ask questions in plain language. Every answer points to the exact page
    it came from, and DocQA says so when the documents do not contain the answer.
  </p>
  <ul class="auth-features">
    <li><div class="auth-feature-icon">📄</div><div>
      <div class="auth-feature-title">Cited answers</div>
      <div class="auth-feature-text">Open any citation to see the page with the passage highlighted.</div></div></li>
    <li><div class="auth-feature-icon">🔒</div><div>
      <div class="auth-feature-title">Private to your account</div>
      <div class="auth-feature-text">Your documents and questions are never visible to other users.</div></div></li>
    <li><div class="auth-feature-icon">🧾</div><div>
      <div class="auth-feature-title">PDF, Word, text and web pages</div>
      <div class="auth-feature-text">Scanned PDFs and tables are supported too.</div></div></li>
  </ul>
</div>
"""
