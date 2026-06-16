import streamlit as st
import pandas as pd
import os
import io
import base64
import sqlite3
import streamlit.components.v1 as components
from bs4 import BeautifulSoup
from svgpathtools import parse_path

# =============================================================================
# 0. ПОДКЛЮЧЕНИЕ ШРИФТОВ GOLOS (ЧЕРЕЗ СТАТИЧЕСКИЕ URL ДЛЯ ИСКЛЮЧЕНИЯ НАГРУЗКИ)
# =============================================================================
font_faces_css = """
@font-face {
  font-family: 'Golos UI';
  src: url('/static/fonts/Golos-UI_VF.woff2') format('woff2'),
       url('/static/fonts/Golos-UI_VF.woff') format('woff');
  font-weight: 100 900;
  font-style: normal;
  font-display: swap;
}
@font-face {
  font-family: 'Golos Text';
  src: url('/static/fonts/golos-text_vf.woff2') format('woff2'),
       url('/static/fonts/golos-text_vf.woff') format('woff');
  font-weight: 100 900;
  font-style: normal;
  font-display: swap;
}
"""

# =============================================================================
# 1. ИНИЦИАЛИЗАЦИЯ ХРАНИЛИЩА И СЧЕТЧИКА ПОСЕЩЕНИЙ
# =============================================================================
def init_counter_db():
    conn = sqlite3.connect('visits.db')
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS counter (id INTEGER PRIMARY KEY, count INTEGER)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS processed_sessions (session_key TEXT PRIMARY KEY)''')
    cursor.execute('SELECT count FROM counter WHERE id = 1')
    if cursor.fetchone() is None:
        cursor.execute('INSERT INTO counter (id, count) VALUES (1, 0)')
    conn.commit()
    conn.close()

def increment_and_get_visits(current_session_key):
    conn = sqlite3.connect('visits.db')
    cursor = conn.cursor()
    cursor.execute('SELECT session_key FROM processed_sessions WHERE session_key = ?', (current_session_key,))
    if cursor.fetchone() is None:
        cursor.execute('INSERT INTO processed_sessions (session_key) VALUES (?)', (current_session_key,))
        cursor.execute('UPDATE counter SET count = count + 1 WHERE id = 1')
        conn.commit()
    cursor.execute('SELECT count FROM counter WHERE id = 1')
    count = cursor.fetchone()[0]
    conn.close()
    return count

init_counter_db()

# =============================================================================
# 2. НАСТРОЙКА СТРАНИЦЫ И ГЛОБАЛЬНОЙ СЕССИИ
# =============================================================================
st.set_page_config(page_title="Информационный портал КФД НСО", layout="wide", page_icon="🏦")

try:
    from streamlit.runtime.scriptrunner import get_script_run_ctx
    ctx = get_script_run_ctx()
    session_id = ctx.session_id if ctx else "default_session"
except Exception:
    session_id = "fallback_session"

query_params = st.query_params
has_region_param = "region" in query_params

if 'visit_counted' not in st.session_state:
    if not has_region_param:
        st.session_state.visit_count = increment_and_get_visits(session_id)
    else:
        conn = sqlite3.connect('visits.db')
        cursor = conn.cursor()
        cursor.execute('SELECT count FROM counter WHERE id = 1')
        res = cursor.fetchone()
        st.session_state.visit_count = res[0] if res else 0
        conn.close()
    st.session_state.visit_counted = True

# =============================================================================
# 3. ОПТИМИЗИРОВАННЫЕ ФУНКЦИИ ДАННЫХ И СБОРКИ EXCEL (КЭШИРОВАНИЕ)
# =============================================================================
def short_region_name(name):
    name = str(name)
    replacements = [" муниципальный район", " городской округ", " муниципальный округ", " район"]
    for rep in replacements:
        name = name.replace(rep, "")
    return name.strip()

@st.cache_data
def load_region_data(file_path):
    if not os.path.exists(file_path):
        return None
    df = pd.read_excel(file_path)
    df['ID'] = df['ID'].astype(str).str.strip()
    
    if "Численность населения, чел." in df.columns:
        df["Численность населения, чел."] = df["Численность населения, чел."].astype(float).round(0).astype(int)
    if "Действующие ФП" in df.columns:
        df["Действующие ФП"] = df["Действующие ФП"].astype(float).round(1)
    return df

@st.cache_data
def load_np_data(file_name):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    cwd = os.getcwd()
    search_dirs = [cwd, script_dir]
    target_path = None
    for d in search_dirs:
        exact = os.path.join(d, file_name)
        if os.path.exists(exact):
            target_path = exact
            break
    if not target_path:
        for d in search_dirs:
            try:
                for fname in os.listdir(d):
                    if fname.lower() == file_name.lower():
                        target_path = os.path.join(d, fname)
                        break
                if target_path: break
            except Exception:
                pass
    if not target_path:
        import glob
        for d in search_dirs:
            matches = glob.glob(os.path.join(d, "DB_01*NP*.xlsx"))
            if matches:
                target_path = matches[0]
                break
    if not target_path:
        return None

    df = pd.read_excel(target_path)
    
    if "Численность населения, чел." in df.columns:
        df["Численность населения, чел."] = df["Численность населения, чел."].apply(
            lambda x: int(round(float(x))) if pd.notna(x) else 0)
    if "Действующие ФП" in df.columns:
        df["Действующие ФП"] = df["Действующие ФП"].apply(
            lambda x: round(float(x), 1) if pd.notna(x) else 0.0)
    if "КФД, в баллах" in df.columns:
        df["КФД, в баллах"] = df["КФД, в баллах"].apply(
            lambda x: round(float(x), 1) if pd.notna(x) else "")
    return df

@st.cache_data
def prepare_svg(svg_path, df_regions):
    with open(svg_path, "r", encoding="utf-8") as f:
        svg_content = f.read()

    soup = BeautifulSoup(svg_content, "xml")
    svg = soup.find("svg")

    if svg.has_attr("width"): del svg["width"]
    if svg.has_attr("height"): del svg["height"]
    svg["preserveAspectRatio"] = "xMidYMid meet"

    region_map = {}
    if df_regions is not None and not df_regions.empty:
        for _, row in df_regions.iterrows():
            region_map[str(row["ID"]).strip()] = short_region_name(row["Район"])

    paths = svg.find_all("path")
    for path in paths:
        if not path.has_attr("id"):
            continue

        path_id = path["id"].strip()
        short_name = region_map.get(path_id, path_id)

        title_tag = soup.new_tag("title")
        title_tag.string = short_name
        path.append(title_tag)

        center_x, center_y = 0, 0
        try:
            d = path.get("d")
            if d:
                svg_path_obj = parse_path(d)
                xmin, xmax, ymin, ymax = svg_path_obj.bbox()
                center_x = (xmin + xmax) / 2
                center_y = (ymin + ymax) / 2

                if short_name == "Куйбышевский":
                    center_y += 18
                    center_x -= 10
                elif short_name == "Доволенский":
                    center_y += 5
                    center_x += 5
                elif short_name == "Карасукский":
                    center_y += 10
                    center_x += 10
        except Exception:
            pass

        parent = path.parent
        if parent.name != "a":
            link_tag = soup.new_tag("a", href=f"?region={path_id}", target="_self")
            path.wrap(link_tag)
            
            if center_x != 0 and center_y != 0:
                text_tag = soup.new_tag("text", x=str(center_x), y=str(center_y), **{"class": "map-label"})
                text_tag.string = short_name
                link_tag.append(text_tag)

    return str(svg)

@st.cache_data
def convert_df_to_excel_b64(df, sheet_name='Sheet1'):
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name=sheet_name)
        worksheet = writer.sheets[sheet_name]
        for idx, col in enumerate(df.columns):
            max_len = max(df[col].astype(str).map(len).max(), len(str(col))) + 2
            worksheet.set_column(idx, idx, max_len)
    return base64.b64encode(buffer.getvalue()).decode()

@st.cache_data
def load_file_to_base64(file_path):
    if os.path.exists(file_path):
        with open(file_path, "rb") as f:
            return base64.b64encode(f.read()).decode()
    return None

SVG_FILE = "NSO.svg"
EXCEL_FILE = "NSO_regions.xlsx"
NP_FILE = "DB_01_07_2025_NP.xlsx"

df_regions = load_region_data(EXCEL_FILE)
df_np_all = load_np_data(NP_FILE)
svg_content = prepare_svg(SVG_FILE, df_regions)

b64_tfd = load_file_to_base64("Как открыть ТФД.zip")
b64_fp = load_file_to_base64("Как назначить ФП.zip")

display_df = df_regions.copy() if df_regions is not None else pd.DataFrame()

# =============================================================================
# 4. ФУНКЦИИ НАВИГАЦИИ И ОПРЕДЕЛЕНИЕ ТЕКУЩЕЙ СТРАНИЦЫ (С ВАЛИДАЦИЕЙ ID РАЙОНА)
# =============================================================================
def go_home():
    st.session_state.page = 'home'
    st.session_state.selected_region = None
    st.query_params.clear()

if has_region_param:
    requested_region_id = str(query_params["region"]).strip()
    
    if df_regions is not None and not df_regions.empty and requested_region_id in df_regions['ID'].astype(str).str.strip().values:
        st.session_state.selected_region = query_params["region"]
        st.session_state.page = 'district'
    else:
        go_home()
        st.rerun()
else:
    if st.session_state.get('page') == 'district' and not has_region_param:
        go_home()
    elif 'page' not in st.session_state:
        st.session_state.page = 'home'
        st.session_state.selected_region = None

# =============================================================================
# 5. CSS СТИЛИЗАЦИЯ (ТИПОГРАФИКА GOLOS + ИНТЕРФЕЙС)
# =============================================================================
st.markdown(f"""
<style>
/* Внедрение шрифтов */
{font_faces_css}

/* Базовые переменные дизайн-системы */
:root {{
    --font-ui: 'Golos UI', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
    --font-text: 'Golos Text', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
}}

/* Глобальный сброс и применение основного шрифта */
body, .stApp, .stMarkdown, .stText, p, span, div {{
    font-family: var(--font-text);
    font-variant-numeric: lining-nums tabular-nums;
}}

/* Уменьшение высоты stMarkdownContainer */
div[data-testid="stMarkdownContainer"] {{
    min-height: 20px !important;
    padding-top: 0rem !important;
    padding-bottom: 0rem !important;
}}

.block-container {{
    padding-top: 0rem !important;
    padding-bottom: 5rem;
    max-width: 100%;
}}

.stAppHeader {{ display: none; }}

.header-container {{
    background: #ffffff;
    border-radius: 16px;
    padding: 24px 20px;
    margin-top: -5px !important;
    margin-bottom: 20px;
    text-align: center;
}}

/* 1. Крупные акценты (Golos UI Bold) */
.main-title h1 {{
    font-family: var(--font-ui);
    font-size: 38px !important;
    font-weight: 700 !important;
    color: #1a252c !important;
    margin: 0 0 10px 0 !important;
    padding: 0 !important;
    line-height: 1.3 !important;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 12px;
    letter-spacing: -0.02em;
}}

.main-title h1 span.icon {{
    font-size: 28px;
    filter: drop-shadow(0 2px 4px rgba(0,0,0,0.1));
}}

/* 2. Интерфейсные мета-данные (Golos UI SemiBold) */
.sub-title h2 {{
    font-family: var(--font-ui);
    font-size: 16px !important;
    font-weight: 600 !important;
    color: #626d7a !important;
    text-transform: uppercase !important;
    letter-spacing: 0.05em !important;
    margin: 0 !important;
    padding: 0 !important;
}}

.left-align-container, .right-align-container {{
    display: flex;
    width: 100%;
}}
.left-align-container {{ justify-content: flex-start; }}
.right-align-container {{ justify-content: flex-end; }}

/* 2. Кнопки и интерактив (Golos UI Medium) */
.portal-btn, div.stButton > button {{
    display: inline-flex !important;
    align-items: center !important;
    justify-content: center !important;
    width: 300px !important; min-width: 300px !important; max-width: 300px !important;
    height: 90px !important; min-height: 90px !important; max-height: 90px !important;
    background-color: rgb(255, 255, 255) !important;
    border: 1px solid rgba(49, 51, 63, 0.2) !important;
    border-radius: 12px !important;
    box-shadow: rgba(0, 0, 0, 0.05) 0px 1px 2px 0px !important;
    margin: 0 !important; padding: 0 !important;
    box-sizing: border-box !important;
    transition: border-color 0.2s, color 0.2s, background-color 0.2s, transform 0.1s !important;
    user-select: none !important; cursor: pointer !important;
    text-decoration: none !important;
}}

.portal-btn,
div.stButton > button,
div.stButton > button p,
div.stButton > button span {{
    font-family: var(--font-ui) !important;
    font-size: 16px !important;
    font-weight: 550 !important;
    font-style: normal !important;
    line-height: 1.2 !important;
    color: rgb(49, 51, 63) !important;
    text-decoration: none !important;
}}

div.stButton > button p {{
    margin: 0 !important;
    padding: 0 !important;
}}

.portal-btn:hover, div.stButton > button:hover,
div.stButton > button:hover p, div.stButton > button:hover span {{
    border-color: rgb(41,128,185) !important; color: rgb(41,128,185) !important;
    background-color: rgb(255, 255, 255) !important;
}}

.portal-btn:active, div.stButton > button:active,
div.stButton > button:active p, div.stButton > button:active span {{
    color: rgb(41,128,185) !important; border-color: rgb(41,128,185) !important;
}}

.portal-btn:hover, div.stButton > button:hover {{
    border-color: #2980b9 !important;
    color: #2980b9 !important;
    transform: translateY(-2px) !important;
    box-shadow: 0 4px 6px -1px rgba(41, 128, 185, 0.1), 0 2px 4px -1px rgba(41, 128, 185, 0.06) !important;
}}

.portal-btn:active, div.stButton > button:active {{
    transform: translateY(1px) !important;
    box-shadow: 0 1px 2px 0 rgba(0, 0, 0, 0.05) !important;
    background-color: #f8fafc !important;
}}

div.stButton {{
    display: flex;
    justify-content: flex-start;
    margin: 0 !important; 
    padding: 0 !important;
}}

div.stButton, [data-testid="stColumn"], [data-testid="stVerticalBlock"] {{
    gap: 0 !important;
}}

.sort-caption {{
    font-family: var(--font-text); font-size: 13px; color: #666;
    margin-top: 5px; margin-bottom: 15px; font-weight: 400;
}}

.contacts-info-card {{
    background-color: #f8f9fa; border-left: 5px solid rgb(41,128,185);
    padding: 20px; border-radius: 8px; margin-top: 20px;
    max-width: 500px; box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    font-size: 14px;
}}

.back-btn-container {{
    margin-top: 50px !important; margin-bottom: 15px !important; display: block !important;
}}
.back-btn-container div.stButton > button {{
    width: auto !important; min-width: 180px !important; max-width: auto !important;
    height: 40px !important; min-height: 40px !important; max-height: 40px !important;
    border-radius: 8px !important; padding: 0 16px !important;
    font-size: 14px !important; font-weight: 550 !important;
}}
.back-btn-container div.stButton {{
    justify-content: flex-start;
}}
.back-btn-container div.stButton > button p,
.back-btn-container div.stButton > button span {{
    font-size: 14px !important;
}}
.back-btn-container div.stButton > button:hover {{ border-color: rgb(41,128,185) !important; color: rgb(41,128,185) !important; }}
.back-btn-container div.stButton > button:hover p,
.back-btn-container div.stButton > button:hover span {{ color: rgb(41,128,185) !important; }}

.back-btn-container div.stButton > button:active {{ border-color: rgb(41,128,185) !important; color: rgb(41,128,185) !important; background-color: #f8fafc !important; transform: translateY(1px) !important; }}
.back-btn-container div.stButton > button:active p,
.back-btn-container div.stButton > button:active span {{ color: rgb(41,128,185) !important; }}

/* Карта */
@keyframes mapEntrance {{
    from {{ opacity: 0; transform: scale(0.95) translateY(10px); }}
    to {{ opacity: 1; transform: scale(1) translateY(0); }}
}}
.svg-wrapper {{ width: 100%; display: flex; justify-content: center; align-items: center; margin-top: 10px; margin-bottom: 70px; overflow: visible; }}
.svg-wrapper svg {{ width: 60%; max-width: 1200px; height: auto !important; max-height: 10%; display: block; overflow: visible !important; }}
.svg-wrapper a {{ text-decoration: none; display: block; outline: none; transform-origin: center !important; transition: transform 0.25s ease, filter 0.25s ease !important; }}
.svg-wrapper path {{ fill: #e0e0e0; stroke: #ffffff; stroke-width: 1; transition: fill 0.25s ease, stroke 0.25s ease !important; cursor: pointer; }}
.map-label {{ font-family: var(--font-ui); font-size: 9px; font-weight: 600; fill: #111111; text-anchor: middle; pointer-events: none; user-select: none; paint-order: stroke; stroke: white; stroke-width: 1.5px; stroke-linejoin: round; }}
.svg-wrapper a:hover {{ transform: scale(1.015) translateY(-2px) !important; filter: drop-shadow(0px 6px 10px rgba(0, 0, 0, 0.3)) !important; position: relative; z-index: 9999 !important; }}
.svg-wrapper a:hover path {{ fill: #3498db !important; stroke: #1f5f8b !important; }}

/* 3. Табличные данные (Golos Text) */
table {{ width: 100% !important; border-collapse: collapse !important; font-size: 14px !important; margin-top: 15px !important; }}
table thead tr th {{
    font-family: var(--font-text); font-weight: 600 !important; font-size: 14px !important;
    background-color: #f8f9fa !important; color: #000000 !important; text-align: center !important;
    border: 1px solid #dcdcdc !important; padding: 10px !important; vertical-align: middle !important;
    position: sticky !important; top: 0 !important; z-index: 100 !important;
    font-variant-numeric: lining-nums tabular-nums;
}}
table tbody tr td {{
    font-family: var(--font-text); font-weight: 400 !important; font-size: 14px !important;
    text-align: center !important; color: #222222 !important; border: 1px solid #dcdcdc !important;
    padding: 6px !important; vertical-align: middle !important;
    font-variant-numeric: lining-nums tabular-nums;
}}
table tbody tr td:first-child {{ text-align: left !important; padding-left: 15px !important; }}
table a {{ color: #0066cc !important; text-decoration: none !important; font-weight: 500 !important; transition: color 0.15s ease; }}
table a:hover {{ color: #004499 !important; text-decoration: underline !important; }}
table tbody tr {{ transition: background-color 0.6s ease; }}
table tbody tr:hover {{ background-color: #f1f7fc !important; cursor: pointer; }}

@keyframes rowPulse {{ 0% {{ background-color: rgba(52, 152, 219, 0.25); }} 100% {{ background-color: transparent; }} }}
.pulse-highlight {{ animation: rowPulse 0.6s ease-out forwards; }}
.sort-arrow {{ display: inline-block; margin-left: 8px; font-size: 15px; vertical-align: middle; }}

.footer {{
    width: calc(100% + 10rem) !important; margin-left: -5rem !important; margin-right: -5rem !important;
    position: relative; 
    background-color: #1e293b; 
    text-align: center;
    padding: 30px 20px 35px 20px; font-size: 15px; 
    color: #cbd5e1; 
    border-top: 1px solid #334155; margin-top: 60px; margin-bottom: -5rem !important;
    font-family: var(--font-text); font-variant-numeric: lining-nums tabular-nums;
}}
.footer strong {{
    color: #38bdf8 !important; 
    background: rgba(56, 189, 248, 0.15); 
    border: 1px solid rgba(56, 189, 248, 0.4); 
    padding: 3px 10px;
    margin-left: 5px; border-radius: 6px; font-weight: 600; display: inline-block;
    font-family: var(--font-ui);
}}
.content-spacer {{ display: none !important; }}
</style>
""", unsafe_allow_html=True)

# =============================================================================
# 6. JS СКРИПТ ДЛЯ ГЛАВНОГО ЭКРАНА
# =============================================================================
sorting_script = """
<script>
const parentDoc = window.parent.document;

try {
    if (parentDoc) {
        parentDoc.documentElement.lang = 'ru';
        const mainAppContainer = parentDoc.querySelector('.block-container') || parentDoc.querySelector('section.main');
        if (mainAppContainer && !mainAppContainer.hasAttribute('role')) {
            mainAppContainer.setAttribute('role', 'main');
        }
    }
} catch (e) {}

function makeSortable(tableId) {
    const table = parentDoc.getElementById(tableId);
    if (!table || !table.tBodies || !table.tBodies[0]) return;

    const tbody = table.tBodies[0];
    const headers = Array.from(table.tHead.rows[0].cells);
    headers.forEach((header, index) => {
        if (header.dataset.sortInitialized === "true") return;
        header.dataset.sortInitialized = "true";

        let asc = true;
        header.style.cursor = "pointer";

        header.onclick = () => {
            const rows = Array.from(tbody.rows);

            rows.sort((a, b) => {
                if (!a.cells[index] || !b.cells[index]) return 0;
                let v1 = a.cells[index].innerText.trim();
                let v2 = b.cells[index].innerText.trim();
                let n1 = parseFloat(v1.replace(",", "."));
                let n2 = parseFloat(v2.replace(",", "."));
                if (!isNaN(n1) && !isNaN(n2)) return asc ? n1 - n2 : n2 - n1;
                return asc ? v1.localeCompare(v2, 'ru') : v2.localeCompare(v1, 'ru');
            });

            rows.forEach(row => tbody.appendChild(row));

            headers.forEach(h => {
                const existingArrow = h.querySelector(".sort-arrow");
                if (existingArrow) existingArrow.remove();
                h.style.setProperty('background-color', '#f8f9fa', 'important');
            });

            if (asc) {
                header.style.setProperty('background-color', '#e8f8f5', 'important');
            } else {
                header.style.setProperty('background-color', '#fdedec', 'important');
            }

            const arrowSpan = parentDoc.createElement("span");
            arrowSpan.className = "sort-arrow";
            arrowSpan.innerHTML = asc ? "&#9650;" : "&#9660;"; 
            arrowSpan.style.color = asc ? "#27ae60" : "#e74c3c";
            header.appendChild(arrowSpan);

            rows.forEach(row => {
                row.classList.remove("pulse-highlight");
                void row.offsetWidth;
                row.classList.add("pulse-highlight");
                setTimeout(() => { row.classList.remove("pulse-highlight"); }, 600);
            });

            asc = !asc;
        };
    });
}

setInterval(() => {
    makeSortable("mainTable");
}, 500);
</script>
"""

# =============================================================================
# СЦЕНАРИЙ №1: ЭКРАН ГЛАВНОЙ СТРАНИЦЫ С КАРТОЙ И ОБЩЕЙ ТАБЛИЦЕЙ
# =============================================================================
if st.session_state.page == 'home':

    st.markdown("""
    <div class="header-container">
        <div class="main-title">
            <h1><span class="icon">🏦</span> Информационный портал финансовой доступности</h1>
        </div>
        <div class="sub-title">
            <h2>Мониторинг муниципальных образований Новосибирской области</h2>
        </div>
    </div>
    """, unsafe_allow_html=True)

    if 'map_animated' not in st.session_state:
        svg_class = "first-load"
        st.session_state.map_animated = True
    else:
        svg_class = ""

    animated_svg = svg_content.replace('<svg ', f'<svg class="{svg_class}" ')

    st.markdown(f'<div class="svg-wrapper">{animated_svg}</div>', unsafe_allow_html=True)
    st.markdown("---")
    st.subheader("Список муниципальных образований")
    st.markdown('<div class="sort-caption">(работает сортировка по нажатию на заголовки)</div>', unsafe_allow_html=True)

    if not display_df.empty:
        display_df_for_table = display_df.copy()
        display_df_for_table["Район"] = display_df_for_table.apply(
            lambda row: f'<a href="?region={row["ID"]}" target="_self">{row["Район"]}</a>',
            axis=1
        )

        html_table = display_df_for_table.drop(columns=["ID"]).to_html(
            escape=False, index=False, table_id="mainTable"
        )
        st.markdown(html_table, unsafe_allow_html=True)

    components.html(sorting_script, height=0)
    st.markdown("<div style='margin-top: 45px;'></div>", unsafe_allow_html=True)

    b64 = convert_df_to_excel_b64(display_df, sheet_name='Районы НСО') if not display_df.empty else ""

    col1, col2 = st.columns([1, 1])

    with col1:
        if st.button("📞 Контакты и обратная связь", key="contacts_btn"):
            st.session_state.show_contacts_block = not st.session_state.get('show_contacts_block', False)
            st.rerun()

    with col2:
        st.markdown(
            f"""
            <div class="right-align-container">
                <a class="portal-btn" href="data:application/vnd.openxmlformats-officedocument.spreadsheetml.sheet;base64,{b64}" download="NSO_regions.xlsx">
                    📥 Выгрузить в Excel
                </a>
            </div>
            """, 
            unsafe_allow_html=True
        )

    if st.session_state.get('show_contacts_block', False):
        st.markdown(
            """
            <div class="contacts-info-card">
                <strong>Контактное лицо:</strong> Селютин Егор Геннадьевич<br><br>
                <strong>Телефон:</strong> (391) 259-06-35<br><br>
                <strong>Email:</strong> <a href="mailto:SelyutinEG@cbr.ru">SelyutinEG@cbr.ru</a>
            </div>
            """, 
            unsafe_allow_html=True
        )

# =============================================================================
# СЦЕНАРИЙ №2: ЭКРАН КАРТОЧКИ КОНКРЕТНОГО ВЫБРАННОГО РАЙОНА
# =============================================================================
elif st.session_state.page == 'district':
    st.markdown("""<style>.footer { margin-top: 30px !important; }</style>""", unsafe_allow_html=True)
    
    region_id = st.session_state.selected_region

    st.markdown('<div class="back-btn-container">', unsafe_allow_html=True)
    if st.button("⬅️ Возврат на главную страницу"):
        go_home()
        st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

    if not display_df.empty:
        region_row = display_df[display_df['ID'].astype(str).str.strip() == str(region_id).strip()]

        if not region_row.empty:
            region_name = region_row['Район'].values[0]

            kfd_base_val = 0.0
            if "КФД, в баллах" in region_row.columns:
                try:
                    kfd_base_val = float(str(region_row["КФД, в баллах"].values[0]).replace(",", "."))
                except Exception:
                    kfd_base_val = 0.0

            st.markdown(f'<h2 style="font-family: var(--font-ui); text-align: center; font-size: 28px !important; font-weight: 700; color: #1a252c; margin-top: 20px; margin-bottom: 5px; letter-spacing: -0.01em;">{region_name}</h2>', unsafe_allow_html=True)
            st.markdown(f'<h3 style="font-family: var(--font-ui); text-align: center; font-size: 16px !important; font-weight: 600; color: #626d7a; margin-top: 0px; margin-bottom: 20px; text-transform: uppercase; letter-spacing: 0.05em;">на 01.07.2025</h3>', unsafe_allow_html=True)

            st.markdown("---")

            NP_COLS = [
                "Населенный пункт",
                "Численность населения, чел.",
                "Офисы банков",
                "Банкоматы КО",
                "Устройства с выдачей наличных",
                "Действующие ТФД",
                "Действующие ФП",
                "КФД, в баллах",
            ]

            df_np_region = pd.DataFrame()
            if df_np_all is None:
                st.error(f"❌ Файл {NP_FILE} не найден.")
            else:
                mask = df_np_all["Район"].astype(str).str.strip() == str(region_name).strip()
                available_cols = [c for c in NP_COLS if c in df_np_all.columns]
                df_np_region = df_np_all[mask][available_cols].copy()
                if "Населенный пункт" in df_np_region.columns:
                    df_np_region = df_np_region.sort_values("Населенный пункт").reset_index(drop=True)

            b64_np_excel = convert_df_to_excel_b64(df_np_region, sheet_name='Населенные пункты') if not df_np_region.empty else ""

            cols_to_show = [col for col in region_row.columns if col != 'ID']
            district_row_data = region_row[cols_to_show].copy()

            dist_headers = list(cols_to_show) + ["Открыть ТФД", "Назначить ФП", "Прогноз бонусного балла", "Прогноз КФД, в баллах"]
            dist_header_html = "".join(f"<th>{c}</th>" for c in dist_headers)

            dist_cells = ""
            for col in cols_to_show:
                val = district_row_data[col].values[0]
                dist_cells += f'<td>{val if pd.notna(val) else ""}</td>'
            
            dist_cells += '<td id="dist-sum-tfd" style="font-weight:600 !important;color:#27ae60;">0</td>'
            dist_cells += '<td id="dist-sum-fp" style="font-weight:600 !important;color:#27ae60;">0</td>'
            dist_cells += f'<td id="dist-bonus-pred" style="font-weight:600 !important;color:#27ae60;">0.0</td>'
            dist_cells += f'<td id="dist-kfd-pred" style="font-weight:600 !important;color:#27ae60;">{kfd_base_val:.1f}</td>'

            np_headers_html = "".join(f"<th>{c}</th>" for c in NP_COLS + ["Открыть ТФД", "Назначить ФП"])

            tooltip_layout_html = """
            <span class="tooltip-container">
                <span class="tooltip-trigger-icon">ⓘ</span>
                <span class="tooltip-box-card">
                <div class="tt-header-title"><span style="color: #2980b9; font-weight: bold; font-size: 20px; margin-right: 5px;">&#8505;</span> Методика расчета показателя «Бонусный балл» (для населенного пункта)</div>                    
                    <div class="tt-description-text">Показатель рассчитывается для каждого населенного пункта индивидуально на основе его текущего уровня КФД и планируемой активности.</div>
                    <div class="tt-formula-block">
                        Бонусный балл НП = Кнужд × (W_тфд × Открыть ТФД + W_фп × Назначить ФП)
                    </div>
                    <ul class="tt-unordered-list">
                        <li><strong>Кнужд</strong> (Коэффициент нужды) = (100 - КФД населенного пункта) / 100</li>
                        <li><strong>Весовые коэффициенты (W_тфд и W_фп)</strong> определяются диапазоном текущего значения КФД:</li>
                    </ul>
                    <table class="tt-embedded-table">
                        <thead>
                            <tr>
                                <th>Текущий КФД НП (в баллах)</th>
                                <th>Вес «Открыть ТФД» (W_тфд)</th>
                                <th>Вес «Назначить ФП» (W_фп)</th>
                            </tr>
                        </thead>
                        <tbody>
                            <tr><td>от 0 до 45</td><td>5</td><td>4</td></tr>
                            <tr><td>от 46 до 70</td><td>4</td><td>3</td></tr>
                            <tr><td>от 71 до 85</td><td>3</td><td>2</td></tr>
                            <tr><td>от 86 до 100</td><td>2</td><td>1</td></tr>
                        </tbody>
                    </table>
                </span>
            </span>
            """
            np_headers_html += f"<th>Бонусный балл {tooltip_layout_html}</th>"

            np_rows_html = ""
            for i, row in df_np_region.iterrows():
                cells = ""
                kfd_val_np = 0.0
                for col in NP_COLS:
                    val = row[col]
                    cells += f'<td>{val if pd.notna(val) else ""}</td>'
                    if col == "КФД, в баллах":
                        try:
                            kfd_val_np = float(str(val).replace(',', '.'))
                        except:
                            kfd_val_np = 0.0
                
                cells += (
                    f'<td style="text-align:center; vertical-align:middle;">'
                    f'<div style="display:inline-flex; align-items:stretch; height:28px;">'
                    f'<input type="text" inputmode="numeric" class="np-open-tfd" '
                    f'style="width:55px; text-align:center; border:1px solid #ccc; border-radius:4px 0 0 4px; '
                    f'padding:0 4px; font-size:14px; color: #27ae60 !important; font-weight:700; outline:none; border-right:none;" oninput="formatTfdInput(event)" value="">'
                    f'<div style="display:flex; flex-direction:column;">'
                    f'<button onclick="stepTfd(event, 1)" style="flex:1; width:15px; font-size:8px; line-height:1; padding:0; border:1px solid #ccc; border-bottom:0.5px solid #ccc; border-radius:0 4px 0 0; background:#f8f9fa; cursor:pointer; color:#555; margin:0;">▲</button>'
                    f'<button onclick="stepTfd(event, -1)" style="flex:1; width:15px; font-size:8px; line-height:1; padding:0; border:1px solid #ccc; border-top:0.5px solid #ccc; border-radius:0 0 4px 0; background:#f8f9fa; cursor:pointer; color:#555; margin:0;">▼</button>'
                    f'</div></div></td>'
                )
                
                cells += (
                    f'<td style="text-align:center; vertical-align:middle;">'
                    f'<div style="display:inline-flex; align-items:stretch; height:28px;">'
                    f'<input type="text" inputmode="decimal" class="np-assign-fp" '
                    f'style="width:55px; text-align:center; border:1px solid #ccc; border-radius:4px 0 0 4px; '
                    f'padding:0 4px; font-size:14px; color: #27ae60 !important; font-weight:700; outline:none; border-right:none;" oninput="formatFpInput(event)" value="">'
                    f'<div style="display:flex; flex-direction:column;">'
                    f'<button onclick="stepFp(event, 0.1)" style="flex:1; width:15px; font-size:8px; line-height:1; padding:0; border:1px solid #ccc; border-bottom:0.5px solid #ccc; border-radius:0 4px 0 0; background:#f8f9fa; cursor:pointer; color:#555; margin:0;">▲</button>'
                    f'<button onclick="stepFp(event, -0.1)" style="flex:1; width:15px; font-size:8px; line-height:1; padding:0; border:1px solid #ccc; border-top:0.5px solid #ccc; border-radius:0 0 4px 0; background:#f8f9fa; cursor:pointer; color:#555; margin:0;">▼</button>'
                    f'</div></div></td>'
                )
                
                cells += f'<td class="np-bonus-val">0</td>'
                np_rows_html += f'<tr data-kfd="{kfd_val_np}">{cells}</tr>\n'

            n_np = len(df_np_region)

            table_css = f"""
<style>
{font_faces_css}

:root {{
    --font-ui: 'Golos UI', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
    --font-text: 'Golos Text', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
}}

* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ background: transparent; padding: 0; overflow-x: auto; font-family: var(--font-text); font-variant-numeric: lining-nums tabular-nums; }}

table {{ width: 100%; border-collapse: collapse; font-size: 14px; margin-top: 10px; font-family: var(--font-text); overflow: visible !important; }}
table thead tr th {{
    font-family: var(--font-text); font-weight: 600; font-size: 14px;
    background-color: #f8f9fa; color: #000000; text-align: center;
    border: 1px solid #dcdcdc; padding: 8px 6px; vertical-align: middle;
    white-space: normal; user-select: none;
    font-variant-numeric: lining-nums tabular-nums;
    position: relative;
}}
table tbody tr td {{
    font-family: var(--font-text); font-weight: 400; font-size: 14px;
    text-align: center; color: #222; border: 1px solid #dcdcdc;
    padding: 5px 6px; vertical-align: middle;
    font-variant-numeric: lining-nums tabular-nums;
}}
table tbody tr td:first-child {{ text-align: left; padding-left: 12px; }}
table tbody tr:hover {{ background-color: #f1f7fc; }}

td.np-bonus-val {{
    font-family: var(--font-ui) !important;
    font-weight: 700 !important;
    color: #27ae60 !important;
    font-size: 14px !important;
}}

h3 {{
    font-family: var(--font-ui); font-size: 17px; font-weight: 600; 
    color: #1a252c; margin-top: 40px; margin-bottom: 4px;
}}
.caption {{ font-family: var(--font-text); font-size: 13px; color: #666; margin-bottom: 8px; }}

input, button {{
    font-family: var(--font-ui) !important;
    font-variant-numeric: lining-nums tabular-nums;
}}

.buttons-container {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-top: 35px;
    margin-bottom: 5px;
    width: 100%;
    flex-wrap: wrap;
    gap: 15px;
}}
.btn-group-left {{
    display: flex;
    gap: 15px;
}}
.btn-group-right {{
    margin-left: auto;
}}
.portal-btn {{
    display: inline-flex !important;
    align-items: center !important;
    justify-content: center !important;
    width: 300px !important; min-width: 300px !important; max-width: 300px !important;
    height: 90px !important; min-height: 90px !important; max-height: 90px !important;
    background-color: rgb(255, 255, 255) !important;
    border: 1px solid rgba(49, 51, 63, 0.2) !important;
    border-radius: 12px !important;
    box-shadow: rgba(0, 0, 0, 0.05) 0px 1px 2px 0px !important;
    margin: 0 !important; padding: 0 !important;
    box-sizing: border-box !important;
    transition: border-color 0.2s, color 0.2s, background-color 0.2s, transform 0.1s !important;
    user-select: none !important; cursor: pointer !important;
    text-decoration: none !important;
    font-family: var(--font-ui) !important;
    font-size: 16px !important;
    font-weight: 550 !important;
    font-style: normal !important;
    line-height: 1.2 !important;
    color: rgb(49, 51, 63) !important;
}}
.portal-btn:hover {{
    background-color: rgb(255, 255, 255) !important;
    border-color: #2980b9 !important;
    color: #2980b9 !important;
    transform: translateY(-2px) !important;
    box-shadow: 0 4px 6px -1px rgba(41, 128, 185, 0.1), 0 2px 4px -1px rgba(41, 128, 185, 0.06) !important;
}}
.portal-btn:active {{
    transform: translateY(1px) !important;
    box-shadow: 0 1px 2px 0 rgba(0, 0, 0, 0.05) !important;
    background-color: #f8fafc !important;
    color: #2980b9 !important;
    border-color: #2980b9 !important;
}}

.tooltip-container {{
    position: relative;
    display: inline-block;
    margin-left: 5px;
    cursor: help;
    vertical-align: middle;
}}

.tooltip-trigger-icon {{
    display: inline-flex;
    align-items: center;
    justify-content: center;
    background-color: #e2e8f0;
    color: #475569;
    border-radius: 50%;
    width: 25px;
    height: 24px;
    font-size: 16px;
    font-weight: bold;
    transition: background-color 0.2s, color 0.2s;
}}

.tooltip-container:hover .tooltip-trigger-icon {{
    background-color: #2980b9;
    color: #ffffff;
}}

.tooltip-box-card {{
    display: none;
    position: absolute;
    top: 25px;
    right: 0;
    width: 600px;
    background-color: #ffffff;
    border: 1px solid #cbd5e1;
    border-radius: 10px;
    box-shadow: 0 10px 25px -5px rgba(0,0,0,0.15), 0 8px 10px -6px rgba(0,0,0,0.1);
    padding: 18px;
    z-index: 999999 !important;
    text-align: left;
    white-space: normal;
    color: #334155;
    font-weight: 400 !important;
}}

.tooltip-container:hover .tooltip-box-card {{
    display: block;
}}

.tt-header-title {{
    font-family: var(--font-ui);
    font-weight: 600;
    font-size: 14px;
    color: #1e293b;
    margin-bottom: 8px;
}}

.tt-description-text {{
    font-size: 13px;
    color: #64748b;
    line-height: 1.45;
    margin-bottom: 12px;
}}

.tt-formula-block {{
    background-color: #f0fdf4;
    border-left: 4px solid #22c55e;
    padding: 10px 12px;
    font-family: var(--font-ui);
    font-weight: 600;
    font-size: 13.5px;
    color: #166534;
    margin-bottom: 12px;
    border-radius: 0 6px 6px 0;
}}

.tt-unordered-list {{
    list-style-type: disc;
    padding-left: 20px;
    font-size: 12.5px;
    color: #334155;
    margin-bottom: 12px;
    line-height: 1.5;
}}

.tt-unordered-list li {{
    margin-bottom: 5px;
}}

.tt-embedded-table {{
    width: 100% !important;
    border-collapse: collapse !important;
    margin-top: 8px !important;
    font-size: 12px !important;
}}

.tt-embedded-table th {{
    background-color: #f8fafc !important;
    color: #475569 !important;
    font-weight: 600 !important;
    border: 1px solid #e2e8f0 !important;
    padding: 6px 8px !important;
    font-size: 11.5px !important;
    cursor: default !important;
    position: static !important;
}}

.tt-embedded-table td {{
    border: 1px solid #e2e8f0 !important;
    padding: 6px 8px !important;
    text-align: center !important;
    color: #334155 !important;
    font-weight: 400 !important;
    font-size: 12px !important;
}}

.tt-embedded-table tbody tr:hover {{
    background-color: #f8fafc !important;
}}
</style>
"""

            np_section_html = ""
            if not df_np_region.empty:
                np_section_html = f"""
<h3>Населённые пункты: {n_np}</h3>
<div class="caption">(работает сортировка по нажатию на заголовки)</div>
<table id="npTable">
  <thead><tr>{np_headers_html}</tr></thead>
  <tbody>{np_rows_html}</tbody>
</table>
"""

            safe_region_name = short_region_name(region_name)
            
            buttons_html = '<div class="buttons-container">'
            buttons_html += '<div class="btn-group-left">'
            if b64_tfd:
                buttons_html += f'<a class="portal-btn" href="data:application/octet-stream;base64,{b64_tfd}" download="Как открыть ТФД.zip">📄 Как открыть ТФД</a>'
            if b64_fp:
                buttons_html += f'<a class="portal-btn" href="data:application/octet-stream;base64,{b64_fp}" download="Как назначить ФП.zip">📄 Как назначить ФП</a>'
            buttons_html += '</div>'
            
            if b64_np_excel:
                buttons_html += f'<div class="btn-group-right"><a class="portal-btn" href="data:application/vnd.openxmlformats-officedocument.spreadsheetml.sheet;base64,{b64_np_excel}" download="{safe_region_name}_НП.xlsx">📥 Выгрузить в Excel</a></div>'
            
            buttons_html += '</div>'

            full_html = f"""
{table_css}
<table id="districtTable">
  <thead><tr>{dist_header_html}</tr></thead>
  <tbody><tr>{dist_cells}</tr></tbody>
</table>
{np_section_html}
{buttons_html}
<script>
const kfdBase = {kfd_base_val};

try {{
    const parentDoc = window.parent.document;
    if (parentDoc) {{
        parentDoc.documentElement.lang = 'ru';
        const mainAppContainer = parentDoc.querySelector('.block-container') || parentDoc.querySelector('section.main');
        if (mainAppContainer && !mainAppContainer.hasAttribute('role')) {{
            mainAppContainer.setAttribute('role', 'main');
        }}
    }}
}} catch (e) {{}}

function sendHeight() {{
    const h = document.body.scrollHeight + 20;
    window.parent.postMessage({{type: "streamlit:setFrameHeight", height: h}}, "*");
}}

function makeSortable(tableId) {{
    const table = document.getElementById(tableId);
    if (!table || !table.tBodies || !table.tBodies[0]) return;
    
    // ГАРАНТИЯ: работаем ТОЛЬКО со строками основного tbody таблицы
    const tbody = table.tBodies[0];
    const headers = Array.from(table.tHead.rows[0].cells);
    headers.forEach((header, index) => {{
        if (header.dataset.sortInitialized === "true") return;
        header.dataset.sortInitialized = "true";
        
        const headerText = header.innerText.trim();
        if (headerText.startsWith("Открыть ТФД") || headerText.startsWith("Назначить ФП") || headerText.includes("Бонусный балл") || headerText.includes("Прогноз")) {{
            header.style.cursor = "default";
            return;
        }}
        
        let asc = true;
        header.style.cursor = "pointer";
        header.onclick = () => {{
            const rows = Array.from(tbody.rows);
            
            rows.sort((a, b) => {{
                if (!a.cells[index] || !b.cells[index]) return 0;
                let v1 = a.cells[index].innerText.trim();
                let v2 = b.cells[index].innerText.trim();
                let n1 = parseFloat(v1.replace(",", "."));
                let n2 = parseFloat(v2.replace(",", "."));
                if (!isNaN(n1) && !isNaN(n2)) return asc ? n1 - n2 : n2 - n1;
                return asc ? v1.localeCompare(v2, "ru") : v2.localeCompare(v1, "ru");
            }});
            
            rows.forEach(row => tbody.appendChild(row));
            
            headers.forEach(h => {{
                const arr = h.querySelector(".sort-arrow");
                if (arr) arr.remove();
                h.style.backgroundColor = "#f8f9fa";
            }});
            
            header.style.backgroundColor = asc ? "#e8f8f5" : "#fdedec";
            const arrowSpan = document.createElement("span");
            arrowSpan.className = "sort-arrow";
            arrowSpan.innerHTML = asc ? "&#9650;" : "&#9660;";
            arrowSpan.style.color = asc ? "#27ae60" : "#e74c3c";
            header.appendChild(arrowSpan);
            
            rows.forEach(row => {{
                row.classList.remove("pulse-highlight");
                void row.offsetWidth;
                row.classList.add("pulse-highlight");
                setTimeout(() => row.classList.remove("pulse-highlight"), 600);
            }});
            
            asc = !asc;
            sendHeight();
        }};
    }});
}}

function formatTfdInput(e) {{
    let val = e.target.value;
    val = val.replace(/[^0-9]/g, '');
    e.target.value = val;
    recalcSums();
}}

function stepTfd(e, delta) {{
    const container = e.target.parentNode.parentNode; 
    const input = container.querySelector('input.np-open-tfd');
    if (!input) return;
    let v = parseInt(input.value);
    if (isNaN(v)) v = 0;
    v += delta;
    if (v < 0) v = 0;
    input.value = v;
    recalcSums();
}}

function formatFpInput(e) {{
    let val = e.target.value;
    val = val.replace(',', '.');
    val = val.replace(/[^0-9.]/g, '');
    const parts = val.split('.');
    if (parts.length > 2) {{
        val = parts[0] + '.' + parts.slice(1).join('');
    }}
    e.target.value = val;
    recalcSums();
}}

function stepFp(e, delta) {{
    const container = e.target.parentNode.parentNode; 
    const input = container.querySelector('input.np-assign-fp');
    if (!input) return;
    let valStr = input.value.replace(',', '.');
    let v = parseFloat(valStr);
    if (isNaN(v)) v = 0;
    v += delta;
    if (v < 0) v = 0;
    v = Math.round(v * 100) / 100; 
    
    let str = v.toFixed(2);
    if (str.endsWith(".00")) str = str.slice(0, -3);
    else if (str.endsWith("0")) str = str.slice(0, -1);
    
    input.value = str;
    recalcSums();
}}

function recalcSums() {{
    let sumTfd = 0;
    let sumFp = 0;
    let totalPredictedBonus = 0;

    const npTable = document.getElementById("npTable");
    if (!npTable || !npTable.tBodies || !npTable.tBodies[0]) return;
    const npRows = Array.from(npTable.tBodies[0].rows);

    npRows.forEach(row => {{
        const tfdInput = row.querySelector('.np-open-tfd');
        const fpInput = row.querySelector('.np-assign-fp');
        const bonusCell = row.querySelector('.np-bonus-val');
        
        let tfd = 0;
        let fp = 0;
        let kfd = parseFloat(row.getAttribute('data-kfd'));
        if (isNaN(kfd)) kfd = 0;

        if (tfdInput) {{
            let v = parseInt(tfdInput.value);
            if (!isNaN(v) && v > 0) tfd = v;
        }}
        if (fpInput) {{
            let v = parseFloat(fpInput.value.replace(',', '.'));
            if (!isNaN(v) && v > 0) fp = v;
        }}

        sumTfd += tfd;
        sumFp += fp;

        let currentRowBonus = 0;

        if (tfd > 0 || fp > 0) {{
            let KoeffNeed = (100 - kfd) / 100;
            let weightTfd = 0;
            let weightFp = 0;

            if (kfd >= 0 && kfd <= 45) {{
                weightTfd = 5; weightFp = 4;
            }} else if (kfd >= 46 && kfd <= 70) {{
                weightTfd = 4; weightFp = 3;
            }} else if (kfd >= 71 && kfd <= 85) {{
                weightTfd = 3; weightFp = 2;
            }} else if (kfd >= 86 && kfd <= 100) {{
                weightTfd = 2; weightFp = 1;
            }}

            currentRowBonus = KoeffNeed * (weightTfd * tfd + weightFp * fp);
        }}
        
        totalPredictedBonus += currentRowBonus;

        if (bonusCell) {{
            let bonusStr = currentRowBonus.toFixed(1);
            if (bonusStr.endsWith(".0")) bonusStr = bonusStr.slice(0, -2);
            bonusCell.innerText = bonusStr;
        }}
    }});

    const tfdCell = document.getElementById("dist-sum-tfd");
    const fpCell  = document.getElementById("dist-sum-fp");
    if (tfdCell) tfdCell.innerText = sumTfd;
    
    if (fpCell) {{
        let roundedFp = Math.round(sumFp * 100) / 100;
        let fpStr = roundedFp.toFixed(2);
        if (fpStr.endsWith(".00")) fpStr = fpStr.slice(0, -3);
        else if (fpStr.endsWith("0")) fpStr = fpStr.slice(0, -1);
        fpCell.innerText = fpStr;
    }}

    const bonusCell = document.getElementById("dist-bonus-pred");
    const kfdPredCell = document.getElementById("dist-kfd-pred");

    if (bonusCell) {{
        let bonusStr = totalPredictedBonus.toFixed(1);
        if (bonusStr.endsWith(".0")) bonusStr = bonusStr.slice(0, -2);
        bonusCell.innerText = bonusStr;
    }}

    let predictedKfd = kfdBase + totalPredictedBonus;
    if (predictedKfd > 100) {{ predictedKfd = 100; }}

    if (kfdPredCell) {{
        let kfdStr = predictedKfd.toFixed(1);
        if (kfdStr.endsWith(".0")) kfdStr = kfdStr.slice(0, -2);
        kfdPredCell.innerText = kfdStr;
    }}
}}

setInterval(() => {{
    makeSortable("districtTable");
    makeSortable("npTable");
}}, 400);

recalcSums();

sendHeight();
setTimeout(sendHeight, 200);
setTimeout(sendHeight, 600);
setTimeout(sendHeight, 1200);
</script>
"""
            iframe_h = 130 + max(1, n_np) * 38 + 280
            components.html(full_html, height=iframe_h, scrolling=False)

# =============================================================================
# 7. ОТОБРАЖЕНИЕ СТАТИЧЕСКОГО ФУТЕРА СО СЧЕТЧИКОМ
# =============================================================================
st.markdown('<div class="content-spacer"></div>', unsafe_allow_html=True)
st.markdown(
    f'''
    <div class="footer">
        🏦 Информационный портал КФД НСО | 
        Данные актуальны на 01.07.2025 | 
        Разработано для органов государственной власти Новосибирской области |
        Посещений портала: <strong>{st.session_state.visit_count}</strong> 
    </div>
    ''',
    unsafe_allow_html=True
)