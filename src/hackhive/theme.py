"""Ink Wash theme for the HackHive Streamlit app. Drop into src/hackhive/theme.py.

Palette: charcoal #4A4A4A (base) · grey #CBCBCB · cream #FFFFE3 · slate #6D8196.
Solid cards: cream (Hubs KPI, Reviewer), slate (Cost J), grey (Coverage strip). Everything else is
soft glass on charcoal. No streamlit import: `page()` does `st.markdown(theme.STYLE, ...)` and
`st.markdown(theme.kpi_html(card), unsafe_allow_html=True)`. A cream rail card is
`st.container(border=True, key="hh_cream_reviewer")` (Streamlit ≥ 1.37 adds `.st-key-<key>`).
"""

from __future__ import annotations

from html import escape

from hackhive.contracts import Constraints, Plan, Sidecar
from hackhive.ui import cost_formula, proven_min_hubs

FONTS = (
    "https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700;800"
    "&family=Instrument+Serif:ital@0;1&family=JetBrains+Mono:wght@500&display=swap"
)

CSS = r"""
:root{--hh-charcoal:#4A4A4A;--hh-grey:#CBCBCB;--hh-cream:#FFFFE3;--hh-slate:#6D8196;
--hh-ink:#262626;--hh-ink-2:#5A5A5A;--hh-danger:#E08A7E;
--hh-glass:rgba(255,255,227,.05);--hh-glass-2:rgba(255,255,227,.09);--hh-hover:rgba(255,255,227,.12);
--hh-well:rgba(0,0,0,.18);--hh-well-2:rgba(0,0,0,.26);
--hh-line:rgba(255,255,227,.13);--hh-line-2:rgba(255,255,227,.24);--hh-edge:rgba(255,255,227,.28);
--hh-fg:#FFFFE3;--hh-fg-2:rgba(255,255,227,.8);--hh-fg-3:rgba(255,255,227,.58);
--hh-sans:'Manrope',system-ui,sans-serif;--hh-serif:'Instrument Serif',Georgia,serif;--hh-mono:'JetBrains Mono',monospace;
--hh-ease:cubic-bezier(.2,.7,.2,1)}

/* canvas: charcoal wash, slate light, faint masked grid */
.stApp{background:
 radial-gradient(620px 620px at 104% -10%,rgba(109,129,150,.7),transparent 70%),
 radial-gradient(520px 520px at -6% -8%,rgba(255,255,227,.07),transparent 70%),
 radial-gradient(700px 460px at 55% 115%,rgba(109,129,150,.45),transparent 70%),
 linear-gradient(150deg,#3A3A3A 0%,#4A4A4A 50%,#56606B 100%)!important;background-attachment:fixed!important}
.stApp::before{content:"";position:fixed;inset:0;pointer-events:none;z-index:0;
 background-image:linear-gradient(rgba(255,255,227,.035) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,227,.035) 1px,transparent 1px);
 background-size:44px 44px;-webkit-mask-image:radial-gradient(ellipse 70% 60% at 50% 40%,#000,transparent);mask-image:radial-gradient(ellipse 70% 60% at 50% 40%,#000,transparent)}
[data-testid="stHeader"]{background:transparent}
html,body,.stApp,[class*="st-"],p,label,li,input,button,textarea{font-family:var(--hh-sans)!important;font-feature-settings:'tnum' 1}
.stApp,.stMarkdown p{color:var(--hh-fg-2)}
code,pre,.stCode{font-family:var(--hh-mono)!important}
h1{font-family:var(--hh-serif)!important;font-weight:400!important;font-size:44px!important;letter-spacing:-.01em;color:var(--hh-fg)!important}
.stApp h1 *{font-family:inherit!important}
h2,h3{font-family:var(--hh-sans)!important;font-weight:700!important;color:var(--hh-fg)!important;letter-spacing:-.01em}
.block-container{padding:3.75rem 1.5rem 3rem!important;max-width:none}
[data-testid="stAppDeployButton"]{display:none}

/* glass: sidebar, bordered containers, tabs */
.st-key-hh_hubs,[data-testid="stTabs"]{
 background:linear-gradient(180deg,var(--hh-glass-2),var(--hh-glass))!important;border:1px solid var(--hh-line)!important;border-top-color:var(--hh-edge)!important;
 border-radius:20px!important;backdrop-filter:blur(26px) saturate(130%);-webkit-backdrop-filter:blur(26px) saturate(130%);
 box-shadow:inset 0 1px 0 rgba(255,255,227,.08),0 28px 56px -30px rgba(0,0,0,.6)}
[data-testid="stSidebar"]{background:transparent!important;border:0!important}
[data-testid="stSidebar"]>div:first-child{margin:14px;height:calc(100% - 28px);border-radius:20px;
 background:linear-gradient(180deg,var(--hh-glass-2),var(--hh-glass))!important;border:1px solid var(--hh-line);border-top-color:var(--hh-edge);
 backdrop-filter:blur(26px) saturate(130%);-webkit-backdrop-filter:blur(26px) saturate(130%);box-shadow:0 28px 56px -30px rgba(0,0,0,.6)}
[data-testid="stSidebar"] h2,[data-testid="stSidebar"] h3{font:700 11px/1 var(--hh-sans)!important;letter-spacing:.14em;text-transform:uppercase;color:var(--hh-fg-3)!important}
[data-testid="stSidebar"] code{color:var(--hh-ink)!important;background:var(--hh-grey)!important;border-radius:8px;padding:2px 8px}
[data-testid="stTabs"]{padding:16px 20px 20px}
hr{border-color:var(--hh-line)!important}

/* solid cream rail card: st.container(border=True, key="hh_cream_…") */
[class*="st-key-hh_cream"]{background:var(--hh-cream)!important;border:0!important;border-radius:20px!important;padding:18px 20px;
 box-shadow:inset 0 1px 0 #fff,0 28px 56px -28px rgba(0,0,0,.55)!important;backdrop-filter:none}
[class*="st-key-hh_cream"] p,[class*="st-key-hh_cream"] li,[class*="st-key-hh_cream"] span{color:var(--hh-ink)}
[class*="st-key-hh_cream"] h2,[class*="st-key-hh_cream"] h3{color:var(--hh-ink)!important}
[class*="st-key-hh_cream"] [data-testid="stCaptionContainer"]{color:var(--hh-ink-2)!important}

/* tabs: dark well, active = cream chip */
[data-testid="stTabs"] [role="tablist"]{gap:4px;padding:4px;border-radius:14px;background:var(--hh-well);border:1px solid var(--hh-line);width:fit-content}
[data-testid="stTab"]{padding:8px 18px!important;border:0!important;border-radius:10px;color:var(--hh-fg-2)!important;transition:all .15s var(--hh-ease)}
[data-testid="stTab"] p{font-weight:600!important;font-size:13px!important;color:inherit!important}
[data-testid="stTab"]:hover{color:var(--hh-fg)!important;background:var(--hh-hover)}
[data-testid="stTab"][aria-selected="true"]{background:var(--hh-cream)!important;color:var(--hh-ink)!important}

/* buttons: secondary = dark well, download + kind=primary = cream */
.stButton>button,.stDownloadButton>button{border-radius:14px!important;padding:.65rem 1rem!important;font-weight:700!important;font-size:13px!important;
 color:var(--hh-fg)!important;background:var(--hh-well)!important;border:1px solid var(--hh-line-2)!important;transition:all .18s var(--hh-ease)!important}
.stButton>button:hover{background:var(--hh-well-2)!important;border-color:var(--hh-grey)!important;transform:translateY(-1px)}
.stButton>button:active,.stDownloadButton>button:active{transform:scale(.98)}
.stButton>button:disabled{opacity:.45;transform:none}
.stDownloadButton>button,.stButton>button[kind="primary"]{color:var(--hh-ink)!important;border-color:transparent!important;background:var(--hh-cream)!important;
 box-shadow:inset 0 1px 0 #fff,0 10px 24px -12px rgba(0,0,0,.6)}
.stDownloadButton>button:hover,.stButton>button[kind="primary"]:hover{background:#FFFFF0!important;transform:translateY(-1px);box-shadow:inset 0 1px 0 #fff,0 16px 30px -12px rgba(0,0,0,.7)}
.stDownloadButton>button p,.stButton>button[kind="primary"] p{color:var(--hh-ink)!important}

/* inputs: dark wells, grey focus */
[data-baseweb="select"]>div,[data-baseweb="input"],[data-testid="stNumberInputContainer"]{background:var(--hh-well)!important;border:1px solid var(--hh-line)!important;border-radius:12px!important;transition:border-color .15s,box-shadow .15s,background .15s}
[data-baseweb="select"]>div:hover,[data-baseweb="input"]:hover,[data-testid="stNumberInputContainer"]:hover{border-color:var(--hh-grey)!important;background:var(--hh-well-2)!important;box-shadow:0 0 0 4px rgba(203,203,203,.14)}
[data-baseweb="select"]>div:focus-within,[data-baseweb="input"]:focus-within,[data-testid="stNumberInputContainer"]:focus-within{border-color:var(--hh-cream)!important;box-shadow:0 0 0 4px rgba(255,255,227,.16)}
[data-baseweb="select"] svg{color:var(--hh-grey)!important}
.stNumberInput:has(input:disabled) :is([data-baseweb="input"],[data-testid="stNumberInputContainer"]){border-style:dashed!important;background:rgba(0,0,0,.12)!important}
.stNumberInput button{background:transparent!important;border:0!important}
[data-baseweb="popover"] ul{background:#3A3A3A!important;border:1px solid var(--hh-line-2);border-radius:12px}
[data-baseweb="popover"] li:hover{background:rgba(109,129,150,.35)!important}
.stRadio [role="radiogroup"]{gap:4px;padding:4px;border-radius:14px;background:var(--hh-well);border:1px solid var(--hh-line);flex-wrap:wrap}
/* one row of equal segments, however many sources there are (ResPlan adds a fourth) */
.stRadio [role="radiogroup"]{display:grid!important;grid-auto-flow:column;grid-auto-columns:minmax(0,1fr);width:100%}
/* as wide as the inputs around them: 1.66 sizes a radio and a button to their content */
[data-testid="stElementContainer"]:has(>.stRadio),.st-key-wi_reset,.st-key-wi_reset button{width:100%!important}
[data-testid="stRadioOption"]{display:flex;justify-content:center;min-width:0;padding:7px 4px!important}
[data-testid="stRadioOption"] p{font-size:13px!important;font-weight:600!important;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.stRadio [role="radiogroup"] label{padding:6px 12px;border-radius:10px;transition:background .15s;margin:0!important}
.stRadio [role="radiogroup"] label:hover{background:var(--hh-hover)}
[data-testid="stRadioOption"]>div>div:first-child:not(:has([data-testid="stMarkdownContainer"])){display:none}
.stRadio [role="radiogroup"] [data-testid="stRadioOption"][data-selected="true"]{background:var(--hh-cream)}.stRadio [role="radiogroup"] [data-testid="stRadioOption"][data-selected="true"] p{color:var(--hh-ink)!important}
[data-testid="stToggle"] [aria-checked="true"],[data-baseweb="checkbox"] [aria-checked="true"]{background:var(--hh-slate)!important}
.stSlider [role="slider"]{background:var(--hh-cream)!important;box-shadow:0 0 0 5px rgba(255,255,227,.16),0 4px 10px rgba(0,0,0,.4)!important}
.stSlider [data-baseweb="slider"]>div>div>div:first-child{background:var(--hh-grey)!important}

/* expanders, alerts, tables */
[data-testid="stExpander"]{border:1px dashed var(--hh-line-2)!important;border-radius:14px!important;background:transparent!important;transition:all .15s}
[data-testid="stExpander"]:hover{border-style:solid!important;border-color:var(--hh-slate)!important;background:rgba(109,129,150,.18)!important}
[data-testid="stAlert"]{border-radius:12px!important}
[data-testid="stAlertContainer"]{color:var(--hh-fg)!important}
[data-testid="stAlertContainer"]:is(:has([data-testid="stAlertContentSuccess"]),:has([data-testid="stAlertContentInfo"])){background:var(--hh-well)!important;border:1px solid var(--hh-line)!important}
[data-testid="stAlertContainer"]:has([data-testid="stAlertContentWarning"]){background:rgba(255,255,227,.1)!important;border:1px solid rgba(255,255,227,.35)!important}
[data-testid="stAlertContainer"]:has([data-testid="stAlertContentError"]){background:rgba(224,138,126,.16)!important;border:1px solid rgba(224,138,126,.5)!important}
[data-testid="stDataFrame"]{border-radius:14px;overflow:hidden;border:1px solid var(--hh-line)}
[data-testid="stImage"] img{border-radius:16px;border:1px solid var(--hh-line)}
[data-testid="stMetric"]{padding:16px 18px;border-radius:16px;background:var(--hh-well);border:1px solid var(--hh-line);transition:transform .2s var(--hh-ease)}
[data-testid="stMetric"]:hover{transform:translateY(-2px)}
[data-testid="stMetricValue"]{font-weight:800!important;letter-spacing:-.03em}
[data-testid="stCaptionContainer"]{color:var(--hh-fg-3)!important}

/* KPI cards (kpi_html). tones: cream · slate · grey (wide strip) · danger */
.hh-kpi{position:relative;overflow:hidden;display:flex;flex-direction:column;gap:14px;padding:20px 22px;min-height:150px;border-radius:20px;
 box-shadow:0 28px 56px -28px rgba(0,0,0,.55);transition:transform .25s var(--hh-ease),box-shadow .25s var(--hh-ease)}
.hh-kpi:hover{transform:translateY(-3px);box-shadow:0 36px 64px -28px rgba(0,0,0,.7)}
.hh-kpi .top{display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap}
.hh-kpi .eb{font:700 11px/1 var(--hh-sans);letter-spacing:.14em;text-transform:uppercase}
.hh-kpi .pill{font:700 11px var(--hh-sans);padding:4px 10px;border-radius:999px;white-space:nowrap}
.hh-kpi .v{font:800 48px/1 var(--hh-sans);letter-spacing:-.035em}
.hh-kpi .u{font:500 15px var(--hh-sans);margin-left:10px}
.hh-kpi .meter{height:6px;border-radius:999px;background:rgba(0,0,0,.12);overflow:hidden}
.hh-kpi .meter i{display:block;height:100%;border-radius:inherit;background:var(--hh-charcoal)}
.hh-kpi .cap{font:500 12px var(--hh-sans)}
.hh-kpi.cream{background:var(--hh-cream);box-shadow:inset 0 1px 0 #fff,0 28px 56px -28px rgba(0,0,0,.55)}
.hh-kpi.cream .v{color:var(--hh-ink)}.hh-kpi.cream .eb,.hh-kpi.cream .u,.hh-kpi.cream .cap{color:var(--hh-ink-2)}
.hh-kpi.cream .pill{background:var(--hh-charcoal);color:var(--hh-cream)}
.hh-kpi.slate{background:linear-gradient(160deg,#7A8DA1,#6D8196);box-shadow:inset 0 1px 0 rgba(255,255,255,.22),0 28px 56px -28px rgba(0,0,0,.6)}
.hh-kpi.slate .v{color:var(--hh-cream)}.hh-kpi.slate .eb,.hh-kpi.slate .u,.hh-kpi.slate .cap{color:rgba(255,255,227,.85)}
.hh-kpi.slate .pill{background:var(--hh-cream);color:var(--hh-ink)}
.hh-kpi.grey{background:var(--hh-grey);min-height:0;display:grid;grid-template-columns:auto minmax(0,1fr);align-items:center;gap:8px 28px;padding:18px 22px;box-shadow:inset 0 1px 0 rgba(255,255,255,.6),0 28px 56px -28px rgba(0,0,0,.55)}
.hh-kpi.grey .v{color:var(--hh-ink);font-size:40px}.hh-kpi.grey .eb,.hh-kpi.grey .u,.hh-kpi.grey .cap{color:var(--hh-ink-2)}
.hh-kpi.grey .pill{background:var(--hh-slate);color:var(--hh-cream)}
.hh-kpi.danger{background:var(--hh-cream)}.hh-kpi.danger .v{color:#B4483A}.hh-kpi.danger .eb,.hh-kpi.danger .u,.hh-kpi.danger .cap{color:var(--hh-ink-2)}
.hh-kpi.danger .pill{background:#B4483A;color:#fff}
@media (max-width:860px){.hh-kpi.grey{grid-template-columns:minmax(0,1fr)}}

/* J breakdown inside the Cost card */
.hh-kpi .terms{display:flex;flex-wrap:wrap;align-items:center;gap:6px}
.hh-kpi .terms div{flex:1 1 68px;min-width:68px}
.hh-kpi .terms div{display:flex;flex-direction:column;gap:2px;padding:8px 10px;border-radius:10px;background:rgba(0,0,0,.14);transition:background .15s}
.hh-kpi .terms div:hover{background:rgba(0,0,0,.24)}
.hh-kpi .terms span{font:700 11px var(--hh-sans);color:rgba(255,255,227,.75)}
.hh-kpi .terms b{font:800 16px var(--hh-sans);color:var(--hh-cream)}
.hh-kpi .terms em{font:500 10px var(--hh-sans);font-style:normal;color:rgba(255,255,227,.65);white-space:nowrap}
.hh-kpi .terms i{display:none}
.hh-kpi .stack{display:flex;gap:3px;height:6px}.hh-kpi .stack i{border-radius:999px}

/* motion: one short entrance per render. Streamlit replays it on every rerun, so it stays under 450ms. */
@keyframes hh-rise{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
@keyframes hh-grow{from{transform:scaleX(0)}to{transform:scaleX(1)}}
.hh-kpi{animation:hh-rise .42s var(--hh-ease) both}
.hh-kpi .meter i,.hh-kpi .stack{transform-origin:left;animation:hh-grow .6s var(--hh-ease) .15s both}
[data-testid="stTabs"] [role="tabpanel"]{animation:hh-rise .3s var(--hh-ease) both}
[class*="st-key-hh_cream"]{animation:hh-rise .42s var(--hh-ease) .06s both}

/* Streamlit 1.66: icons keep their font, the style-only markdown takes no room, our HTML keeps
   its gap, case-tag chips in slate */
.stApp :is([data-testid="stIconMaterial"],[data-testid="stAlertDynamicIcon"]){font-family:"Material Symbols Rounded"!important;font-feature-settings:normal}
[data-testid="stElementContainer"]:has([data-testid="stMarkdownContainer"] > style){display:none}
[data-testid="stMarkdownContainer"]:has(> .hh-kpi){margin-bottom:0}
.stApp .stMarkdownBadge{background:var(--hh-slate)!important;color:var(--hh-cream)!important;border-radius:999px;padding:3px 10px;font:700 11px var(--hh-sans)!important}

/* the rail (st.container(key="hh_rail")): right of the page at >= 1280 px, as tall as it */
@media (min-width:1280px){
 [data-testid="stMainBlockContainer"]>[data-testid="stVerticalBlock"]:has(.st-key-hh_cream_reviewer){position:relative;padding-right:358px}
 .st-key-hh_rail{position:absolute;top:0;right:0;bottom:0;width:340px}
 .st-key-hh_rail>:has(>.st-key-hh_rail_cards){flex:1 1 auto}  /* full height, so the cards can stick */
 .st-key-hh_rail_cards{position:sticky;top:3.75rem;max-height:calc(100vh - 5rem);overflow:auto}
}
.st-key-hh_rail_cards{gap:18px}
.st-key-hh_hubs{padding:18px 20px}

/* KPI row: both cards as tall as the taller one */
[data-testid="stHorizontalBlock"]:has(.hh-kpi){align-items:stretch}
[data-testid="stColumn"]:has(.hh-kpi) :is([data-testid="stVerticalBlock"],[data-testid="stElementContainer"],[data-testid="stMarkdown"],[data-testid="stMarkdown"]>div,[data-testid="stMarkdownContainer"]),
[data-testid="stColumn"] .hh-kpi{height:100%}

/* rail cards: eyebrow headings, the verdict large (the line before the metrics caption),
   compact checklist rows that highlight */
:is([class*="st-key-hh_cream"],.st-key-hh_hubs) h3{font:700 11px/1.2 var(--hh-sans)!important;letter-spacing:.14em;text-transform:uppercase;padding:0 0 4px!important}
[class*="st-key-hh_cream"] h3{color:var(--hh-ink-2)!important}.st-key-hh_hubs h3{color:var(--hh-fg-3)!important}
[class*="st-key-hh_cream"]{gap:.35rem}
[class*="st-key-hh_cream"] [data-testid="stElementContainer"]:has([data-testid="stMarkdown"]){padding:4px 8px;border-radius:10px;transition:background .15s}
[class*="st-key-hh_cream"] [data-testid="stElementContainer"]:has([data-testid="stMarkdown"]):hover{background:rgba(74,74,74,.07)}
[class*="st-key-hh_cream"] [data-testid="stElementContainer"]:has(+[data-testid="stElementContainer"] [data-testid="stCaptionContainer"]) p{font:800 22px/1.2 var(--hh-sans)!important;letter-spacing:-.01em}

/* entrances, staggered 60ms: Hubs, Cost J, the reviewer, then the strip and the per-hub card */
[data-testid="stColumn"]:nth-child(2) .hh-kpi{animation-delay:.06s}
.hh-kpi.grey,.st-key-hh_hubs{animation:hh-rise .42s var(--hh-ease) .12s both}

/* motion+: slow drifting light behind the glass, a light sweep over the solid cards on hover,
   a glow under the active tab */
.stApp{isolation:isolate}
.stApp::before{z-index:-1}
.stApp::after{content:"";position:fixed;inset:-25%;pointer-events:none;z-index:-1;will-change:transform;
 background:radial-gradient(30% 30% at 25% 35%,rgba(109,129,150,.34),transparent 70%),radial-gradient(26% 26% at 75% 65%,rgba(255,255,227,.06),transparent 70%);
 animation:hh-drift 36s ease-in-out infinite alternate}
@keyframes hh-drift{to{transform:translate3d(7%,-5%,0) rotate(6deg)}}
[class*="st-key-hh_cream"]{position:relative;overflow:hidden}
.hh-kpi::after,[class*="st-key-hh_cream"]::after{content:"";position:absolute;inset:0;border-radius:inherit;pointer-events:none;
 background:linear-gradient(115deg,transparent 30%,rgba(255,255,255,.32) 48%,transparent 66%);transform:translateX(-130%);transition:transform .9s var(--hh-ease)}
.hh-kpi:hover::after,[class*="st-key-hh_cream"]:hover::after{transform:translateX(130%)}
[data-testid="stTab"][aria-selected="true"]{box-shadow:0 0 0 1px rgba(255,255,227,.6),0 8px 26px -8px rgba(255,255,227,.55)}

@media (prefers-reduced-motion:reduce){*,*::before,*::after{transition:none!important;animation:none!important}}
"""

# one <style> block, fonts by @import: markdown ends a block that starts with <link> at the
# first blank line, and CSS has blank lines; a <style> block runs to </style>
STYLE = f"<style>@import url('{FONTS}');{CSS}</style>"


def kpi_html(card: dict) -> str:
    """One KPI card: {tone, label, value, unit?, pill?, meter? (0-1), caption?, terms?, stack?}.
    `terms` = [(symbol, value, note)] renders the J breakdown tiles. All text escaped.
    tone: cream, slate, grey (the wide Coverage strip) or danger."""
    unit = f'<span class="u">{escape(card["unit"])}</span>' if card.get("unit") else ""
    pill = f'<span class="pill">{escape(card["pill"])}</span>' if card.get("pill") else ""
    meter = ""
    if card.get("meter") is not None:
        pct = max(0.0, min(1.0, card["meter"])) * 100
        meter = f'<div class="meter"><i style="width:{pct:.0f}%"></i></div>'
    cap = f'<span class="cap">{escape(card["caption"])}</span>' if card.get("caption") else ""
    if card.get("terms"):  # [(symbol, value, note), ...]: one tile per term of J
        tiles = "<i>+</i>".join(
            f"<div><span>{escape(s)}</span><b>{escape(v)}</b><em>{escape(n)}</em></div>"
            for s, v, n in card["terms"]
        )
        cap = f'<div class="terms">{tiles}</div>' + cap
    if card.get("stack"):  # [(share, css colour), ...]: the bar under the terms
        bars = "".join(
            f'<i style="flex:{float(w):.4f};background:{escape(col)}"></i>'
            for w, col in card["stack"]
            if w and w > 0
        )
        cap += f'<div class="stack">{bars}</div>'
    label = f'<span class="eb">{escape(card["label"])}</span>'
    value = f'<div><span class="v">{escape(str(card["value"]))}</span>{unit}</div>'
    if card["tone"] == "grey":  # wide strip: label + value left, caption + pill + meter right
        right = f'<div class="top">{cap}{pill}</div>{meter}'
        return (
            f'<div class="hh-kpi grey"><div style="display:flex;flex-direction:column;gap:10px">{label}{value}</div>'
            f'<div style="display:flex;flex-direction:column;gap:10px">{right}</div></div>'
        )
    return (
        f'<div class="hh-kpi {escape(card["tone"])}"><div class="top">{label}{pill}</div>'
        f"{value}{meter}{cap}</div>"
    )


DASH = "–"
STACK_COLOURS = (
    "#FFFFE3",
    "#6D8196",
    "#4A4A4A",
    "#CBCBCB",
)  # L, X, O, B: cream, slate, charcoal, grey


def kpi_cards(side: Sidecar | None, plan: Plan | None, c: Constraints) -> list[dict]:
    """The three cards for `kpi_html`, in page order: Hubs (H vs LB) and Cost J above the tabs,
    the Coverage strip below. Every value is "–", with no meter, without a plan or a design; an
    infeasible or input_error plan turns Hubs `danger`, "✗", with the reason as its caption.
    The J terms come from `c.cost_weights`, so they always add up to J."""
    w = c.cost_weights
    formula = cost_formula(w)  # slide 6's formula, written from the weights
    hubs = {"tone": "cream", "label": "Hubs · H vs LB", "value": DASH}
    cost = {"tone": "slate", "label": "Cost J", "value": DASH, "unit": formula.removeprefix("J ")}
    coverage = {"tone": "grey", "label": "Coverage", "value": DASH}
    if side is not None and side.status in ("infeasible", "input_error"):
        hubs.update(tone="danger", value="✗", caption=side.reason or side.status)
    b, m = (side.bounds, side.cost) if side else (None, None)
    if plan is None or b is None or b.k_found is None or m is None:
        return [hubs, cost, coverage]
    k, lb = b.k_found, b.k_lb
    proven = proven_min_hubs(side) is not None
    hubs.update(
        value=str(k),
        unit=f"of LB {lb}",
        pill="✓ proven min" if proven else f"+{k - lb} over LB",
        meter=lb / k,
    )
    parts = (w.length * m.L, w.cross * m.X, w.overlap * m.O, w.bend * m.B)
    notes = ("length m", f"X = {m.X}", "overlap m", f"B = {m.B}")
    symbols = formula.removeprefix("J = ").split(" + ")
    cost.update(
        value=f"{m.J:.2f}",
        pill="lower is better",
        terms=[(s, f"{v:.2f}", n) for s, v, n in zip(symbols, parts, notes, strict=True)],
        stack=list(zip(parts, STACK_COLOURS, strict=True)),
    )
    area, n = sum(r.area_m2 for r in plan.rooms if r.eligible), len(plan.nodes)  # plan_facts'
    cap = k * c.max_area_m2
    coverage.update(
        value=f"{area:.1f}",
        unit=f"m² · {n} nodes",
        pill="eligible",
        meter=area / cap,
        caption=f"{area / cap:.0%} of {k} hub{'s' * (k != 1)} × {c.max_area_m2:g} m² cap",
    )
    return [hubs, cost, coverage]
