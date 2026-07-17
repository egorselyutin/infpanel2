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

def set_date_param():
    """Callback для сохранения выбранной даты в URL при изменении селектора"""
    if "selected_date" in st.session_state:
        st.query_params["date"] = st.session_state.selected_date

query_params = st.query_params
has_region_param = "region" in query_params

# Считываем текущую дату из URL (если её нет — ставим по умолчанию)
current_date = query_params.get("date", "01.07.2025")
if isinstance(current_date, list):
    current_date = current_date[0]

# Синхронизируем стейт виджета с датой из URL (чтобы не было лишних перезагрузок)

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
            matches = glob.glob(os.path.join(d, "DB_*_NP.xlsx"))
            if matches:
                target_path = matches[0]
                break
    if not target_path:
        return None

    df = pd.read_excel(target_path)
    
    if "Численность населения, чел." in df.columns:
        df["Численность населения, чел."] = df["Численность населения, чел."].apply(
            lambda x: int(round(float(x))) if pd.notna(x) else 0)
    return df

@st.cache_data
def load_main_indicators(file_path):
    if not os.path.exists(file_path):
        return None
    df = pd.read_excel(file_path, header=None)
    return df

@st.cache_data
def load_nso_summary_data(file_path):
    """Загрузка общих показателей по НСО из файла NSO_ДД.ММ.ГГГГ.xlsx"""
    if not os.path.exists(file_path):
        return None
    df = pd.read_excel(file_path)
    if "Численность населения, чел." in df.columns:
        df["Численность населения, чел."] = df["Численность населения, чел."].astype(float).round(0).astype(int)
    if "Действующие ФП" in df.columns:
        df["Действующие ФП"] = df["Действующие ФП"].astype(float).round(1)
    return df

@st.cache_data
def prepare_svg(svg_path, df_regions, current_date):
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
            link_tag = soup.new_tag("a", href=f"?region={path_id}&date={current_date}", target="_self")
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

# =============================================================================
# ДИНАМИЧЕСКОЕ ОПРЕДЕЛЕНИЕ ФАЙЛОВ
# =============================================================================
date_suffix = f"_{current_date}"

SVG_FILE = f"NSO{date_suffix}.svg"
EXCEL_FILE = f"NSO_regions{date_suffix}.xlsx"
NP_FILE = f"DB{date_suffix}_NP.xlsx"
MAIN_INDICATORS_FILE = f"main_indicators{date_suffix}.xlsx"
NSO_SUMMARY_FILE = f"NSO{date_suffix}.xlsx"

df_regions = load_region_data(EXCEL_FILE)
df_np_all = load_np_data(NP_FILE)
df_indicators = load_main_indicators(MAIN_INDICATORS_FILE)
df_nso_summary = load_nso_summary_data(NSO_SUMMARY_FILE)
svg_content = prepare_svg(SVG_FILE, df_regions, current_date)

b64_tfd = load_file_to_base64("Как открыть ТФД.zip")
b64_fp = load_file_to_base64("Как назначить ФП.zip")

display_df = df_regions.copy() if df_regions is not None else pd.DataFrame()

# =============================================================================
# 4. ФУНКЦИИ НАВИГАЦИИ И ОПРЕДЕЛЕНИЯ ТЕКУЩЕЙ СТРАНИЦЫ
# =============================================================================
def go_home():
    st.session_state.page = 'home'
    st.session_state.selected_region = None
    if "region" in query_params:
        del query_params["region"]

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
# 5. CSS СТИЛИЗАЦИЯ
# =============================================================================
st.markdown(f"""
<style>
/* Внедрение шрифтов */
{font_faces_css}

:root {{
    --font-ui: 'Golos UI', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
    --font-text: 'Golos Text', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
}}

body, .stApp, .stMarkdown, .stText, p, span, div {{
    font-family: var(--font-text);
    font-variant-numeric: lining-nums tabular-nums;
}}

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
    padding: 24px 20px 10px 20px;
    margin-top: -5px !important;
    margin-bottom: 20px;
    text-align: center;
}}

.main-title h1 {{
    font-family: var(--font-ui);
    font-size: 38px !important;
    font-weight: 700 !important;
    color: #1a252c !important;
    margin: 0 0 15px 0 !important;
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

.sub-title {{
    text-align: center !important;
}}
.sub-title h4 {{
    font-family: var(--font-ui);
    font-size: 18px !important;
    font-weight: 600 !important;
    color: #1a252c !important;
    margin: 0 0 5px 0 !important;
    padding: 0 !important;
    text-align: center !important;
}}
.sub-title p {{
    font-size: 14px !important;
    color: #626d7a !important;
    margin: 0 !important;
    text-align: center !important;
}}

/* Стилизация селектора даты (убран мигающий курсор и выделение текста) */
.date-picker-wrapper [data-baseweb="select"] {{
    background-color: #f8f9fa !important;
    border: 1px solid #e2e8f0 !important;
    border-radius: 8px !important;
    font-family: var(--font-ui) !important;
    font-size: 15px !important;
    font-weight: 500 !important;
    color: #1a252c !important;
    height: 40px !important;
    padding-top: 8px !important;
    padding-left: 10px !important;
    box-shadow: none !important;
    margin: 0 auto !important;
    cursor: pointer !important;
    caret-color: transparent !important;
    user-select: none !important;
}}
.date-picker-wrapper [data-baseweb="select"]:hover {{
    border-color: #2980b9 !important;
}}
.date-picker-wrapper [data-baseweb="select"] svg {{
    fill: #1a252c !important;
}}
.date-picker-wrapper [data-baseweb="select"] input {{
    cursor: pointer !important;
    caret-color: transparent !important;
    -webkit-user-select: none;  
    -moz-user-select: none; 
    -ms-user-select: none; 
    pointer-events: none !important;
    user-select: none !important;     
}}

.left-align-container, .right-align-container {{
    display: flex;
    width: 100%;
}}
.left-align-container {{ justify-content: flex-start; }}
.right-align-container {{ justify-content: flex-end; }}

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
    font-family: var(--font-text); 
    font-size: 13px; 
    color: #666;
    margin-top: 5px; 
    margin-bottom: 15px; 
    font-weight: 400;
    text-align: center;
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

@keyframes mapEntrance {{
    from {{ opacity: 0; transform: scale(0.95) translateY(10px); }}
    to {{ opacity: 1; transform: scale(1) translateY(0); }}
}}
.svg-wrapper {{ width: 100%; display: flex; justify-content: center; align-items: center; margin-top: 10px; margin-bottom: 15px; overflow: visible; }}
.svg-wrapper svg {{ width: 100%; max-width: 100%; height: auto !important; max-height: none; display: block; overflow: visible !important; }}
.svg-wrapper a {{ text-decoration: none; display: block; outline: none; transform-origin: center !important; transition: transform 0.25s ease, filter 0.25s ease !important; }}
.svg-wrapper path {{ fill: #e0e0e0; stroke: #ffffff; stroke-width: 1; transition: fill 0.25s ease, stroke 0.25s ease !important; cursor: pointer; }}
.map-label {{ font-family: var(--font-ui); font-size: 9px; font-weight: 600; fill: #111111; text-anchor: middle; pointer-events: none; user-select: none; paint-order: stroke; stroke: white; stroke-width: 1.5px; stroke-linejoin: round; }}
.svg-wrapper a:hover {{ transform: scale(1.015) translateY(-2px) !important; filter: drop-shadow(0px 6px 10px rgba(0, 0, 0, 0.3)) !important; position: relative; z-index: 9999 !important; }}
.svg-wrapper a:hover path {{ fill: #3498db !important; stroke: #1f5f8b !important; }}

.indicators-container {{ padding: 10px 5px; }}
.indicator-section-title {{ text-align: center; font-family: var(--font-ui); font-size: 18px; font-weight: 600; color: #1a252c; margin: 0px 0 12px 0; }}
.indicator-section-subtitle-italic {{ text-align: center; font-family: var(--font-text); font-size: 14px; font-style: italic; color: #626d7a; margin: -8px 0 12px 0; }}
.indicator-row {{ display: flex; gap: 10px; margin-bottom: 35px; }}
.indicator-card {{ flex: 1; background: #f8f9fa; border: 1px solid #e2e8f0; border-radius: 12px; padding: 12px 8px; text-align: center; display: flex; flex-direction: column; justify-content: center; min-height: 75px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }}
.card-line1 {{ font-family: var(--font-ui); font-size: 18px; font-weight: 600; color: #2980b9; line-height: 1.3; }}
.card-line2 {{ font-family: var(--font-text); font-size: 18px; font-weight: 600; color: #64748b; margin-top: 4px; margin-bottom: 4px; line-height: 1.3; }}
.card-line3 {{ font-family: var(--font-text); font-size: 14px; color: #64748b; line-height: 1.3; }}

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

.district-section-title {{
    font-family: var(--font-ui); font-size: 17px; font-weight: 600; 
    color: #1a252c; margin-top: 1px; margin-bottom: 12px;
    text-align: center;
}}
.district-section-caption {{
    font-family: var(--font-text); font-size: 13px; color: #666; margin-bottom: 8px;
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

/* Стили для новой таблицы весов */

.weights-table {{
    margin-top: 5px !important;
/*    border: 1px solid #e2e8f0 !important;*/
}}
.weights-table thead tr th {{
    background-color: #f8fafc !important;
}}
.weights-table tbody tr td {{
/*    border: 1px solid #edf2f7 !important;*/
    padding: 10px !important;
/*    text-align: center !important;    */
}}
/* Выделение группирующих заголовков */
.weights-table thead tr:first-child th {{
    background-color: #f1f5f9 !important;
/*    font-weight: 600 !important;*/
/*    color: #334155 !important;*/
    padding: 12px !important;
}}
#weights tbody tr td:first-child {{
    text-align: center !important;
}}

/* Удаляем выделение заголовков */
th {{
    -webkit-user-select: none; /* Для Safari */
    -moz-user-select: none;    /* Для Firefox */
    -ms-user-select: none;     /* Для IE/Edge */
    user-select: none;         /* Стандартный вариант */
    cursor: pointer;           /* Указывает пользователю, что элемент кликабелен */
}}

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
# 6. JS СКРИПТ ДЛЯ СОРТИРОВКИ ТАБЛИЦ
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

function lockSelectInput() {
    const inputs = parentDoc.querySelectorAll('.date-picker-wrapper input');
    inputs.forEach(input => {
        if (!input.readOnly) {
            input.readOnly = true;
        }
        input.style.caretColor = 'transparent';
        input.style.cursor = 'pointer';
    });
}

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
            if (asc) { header.style.setProperty('background-color', '#e8f8f5', 'important'); }
            else { header.style.setProperty('background-color', '#fdedec', 'important'); }
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
    makeSortable("npTable");
    lockSelectInput();
}, 500);
</script>
"""

# =============================================================================
# ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ ДЛЯ ФОРМИРОВАНИЯ HTML ПРАВОЙ ЧАСТИ
# =============================================================================
def build_indicators_html(df_ind):
    if df_ind is None:
        return '<div style="padding:20px;text-align:center;color:#999;">Файл основных показателей не найден</div>'
    def cell(row, col):
        try:
            val = df_ind.iloc[row, col]
            if pd.isna(val): return ""
            if isinstance(val, float):
                if val == int(val): return str(int(val))
                return str(val)
            return str(val)
        except Exception: return ""
    html = '<div class="indicators-container">'
    html += '<div class="indicator-section-title">Основные показатели финансовой доступности</div>'
    html += '<div class="indicator-row">'
    html += f'<div class="indicator-card"><div class="card-line1">{cell(0,0)}</div><div class="card-line2">{cell(1,0)}</div><div class="card-line3">{cell(2,0)}</div></div>'
    html += f'<div class="indicator-card"><div class="card-line1">{cell(0,1)}</div><div class="card-line2">{cell(1,1)}</div><div class="card-line3">{cell(2,1)}</div></div>'
    html += '</div>'
    html += '<div class="indicator-section-title">Платежная инфраструктура</div>'
    html += '<div class="indicator-row">'
    html += f'<div class="indicator-card"><div class="card-line1">{cell(0,2)}</div><div class="card-line2">{cell(1,2)}</div><div class="card-line3">{cell(2,2)}</div></div>'
    html += f'<div class="indicator-card"><div class="card-line1">{cell(0,3)}</div><div class="card-line2">{cell(1,3)}</div><div class="card-line3">{cell(2,3)}</div></div>'
    html += '</div><div class="indicator-row">'
    html += f'<div class="indicator-card"><div class="card-line1">{cell(0,4)}</div><div class="card-line2">{cell(1,4)}</div><div class="card-line3">{cell(2,4)}</div></div>'
    html += f'<div class="indicator-card"><div class="card-line1">{cell(0,5)}</div><div class="card-line2">{cell(1,5)}</div><div class="card-line3">{cell(2,5)}</div></div>'
    html += '</div>'
    html += '<div class="indicator-section-title">Альтернативная инфраструктура</div>'
    html += '<div class="indicator-section-subtitle-italic">(создана при непосредственном участии Сибирского ГУ Банка России)</div>'
    html += '<div class="indicator-row">'
    html += f'<div class="indicator-card"><div class="card-line1">{cell(0,6)}</div><div class="card-line2">{cell(1,6)}</div><div class="card-line3">{cell(2,6)}</div></div>'
    html += f'<div class="indicator-card"><div class="card-line1">{cell(0,7)}</div><div class="card-line2">{cell(1,7)}</div><div class="card-line3">{cell(2,7)}</div></div>'
    html += '</div></div>'
    return html

# =============================================================================
# СЦЕНАРИЙ №1: ГЛАВНАЯ СТРАНИЦА
# =============================================================================
if st.session_state.page == 'home':
    st.markdown("""
    <div class="header-container">
        <div class="main-title">
            <h1><span class="icon">🏦</span> Информационный портал финансовой доступности</h1>
        </div>
        <div class="sub-title">
            <h4>Новосибирская область</h4>
            <p>30 муниципальных образований, 876 населенных пунктов (без учета городов и с численностью населения от 100 человек)</p>
        </div>
    </div>
    """, unsafe_allow_html=True)

    _, center_col, _ = st.columns([5.8, 1, 6])
    with center_col:
        st.markdown('<div class="date-picker-wrapper">', unsafe_allow_html=True)
        dates_list = ["01.07.2025", "01.01.2026"]
        st.selectbox(
            "Отчетная дата",
            dates_list,
            label_visibility="collapsed",
            key="selected_date",
            index=dates_list.index(current_date) if current_date in dates_list else 0,
            on_change=set_date_param
        )

        st.markdown('</div>', unsafe_allow_html=True)

    if 'map_animated' not in st.session_state:
        svg_class = "first-load"
        st.session_state.map_animated = True
    else:
        svg_class = ""

    animated_svg = svg_content.replace('<svg ', f'<svg class="{svg_class}" ')
    indicators_html = build_indicators_html(df_indicators)

    left_col, spacer, right_col = st.columns([3, 0.2, 1.8])
    with left_col:
        st.markdown(f'<div class="svg-wrapper">{animated_svg}</div>', unsafe_allow_html=True)
    with spacer:
        st.empty()
    with right_col:
        st.markdown(indicators_html, unsafe_allow_html=True)
        
    st.markdown("---")
    st.markdown("""
        <div style="text-align: center;">
            <h2 style="margin-bottom: 0;">Список муниципальных образований</h2>
            <div class="sort-caption" style="margin-top: -15px;">(работает сортировка по нажатию на заголовки)</div>
        </div>
    """, unsafe_allow_html=True)    

    if not display_df.empty:
        display_df_for_table = display_df.copy()
        display_df_for_table["Район"] = display_df_for_table.apply(
            lambda row: f'<a href="?region={row["ID"]}&date={current_date}" target="_self">{row["Район"]}</a>',
            axis=1
        )

        headers_html = "".join(f"<th>{c}</th>" for c in display_df_for_table.drop(columns=["ID"]).columns)
        rows_html = ""
        for _, row in display_df_for_table.iterrows():
            cells = ""
            for col in display_df_for_table.drop(columns=["ID"]).columns:
                val = row[col]
                if str(col).startswith(("Уровень", "Изменение уровня")) and pd.notna(val):
                    try:
                        num_val = float(str(val).replace('%', '').replace(',', '.').strip())
                        if num_val <= 1.0: num_val *= 100
                        cells += f'<td>{num_val:.1f}%</td>'
                    except:
                        cells += f'<td>{val}</td>'
                else:
                    cells += f'<td>{val if pd.notna(val) else ""}</td>'
            rows_html += f"<tr>{cells}</tr>"
        
        st.markdown(f'<table id="mainTable"><thead><tr>{headers_html}</tr></thead><tbody>{rows_html}</tbody></table>', unsafe_allow_html=True)

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
# СЦЕНАРИЙ №2: СТРАНИЦА РАЙОНА
# =============================================================================
elif st.session_state.page == 'district':
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

            st.markdown(f'<h2 style="font-family: var(--font-ui); text-align: center; font-size: 28px !important; font-weight: 700; color: #1a252c; margin-top: 20px; margin-bottom: 5px; letter-spacing: -0.01em;">{region_name}</h2>', unsafe_allow_html=True)

            cols_to_show = [col for col in region_row.columns if col != 'ID']
            np_display_cols = []
            for col in cols_to_show:
                if col == "Район":
                    np_display_cols.append("Населенный пункт")
                else:
                    np_display_cols.append(col)

            df_np_region = pd.DataFrame()
            if df_np_all is None:
                st.error(f"❌ Файл {NP_FILE} не найден.")
            else:
                mask = df_np_all["Район"].astype(str).str.strip() == str(region_name).strip()
                available_cols = [c for c in np_display_cols if c in df_np_all.columns]
                df_np_region = df_np_all[mask][available_cols].copy()
                if "Населенный пункт" in df_np_region.columns:
                    df_np_region = df_np_region.sort_values("Населенный пункт").reset_index(drop=True)

            b64_np_excel = convert_df_to_excel_b64(df_np_region, sheet_name='Населенные пункты') if not df_np_region.empty else ""

            st.markdown(f'<div class="district-section-title">Количество населенных пунктов: {len(df_np_region)}</div>', unsafe_allow_html=True)
            st.markdown('<div style="font-family: var(--font-ui); font-size: 16px; font-weight: 600; color: #1a252c; margin-top: 10px; margin-bottom: 8px; text-align: center;">(без городов и с численностью населения от 100 чел.)</div>', unsafe_allow_html=True)            
            st.markdown(f'<h5 style="font-family: var(--font-ui); text-align: center; font-size: 16px !important; font-weight: 600; color: #1a252c; margin-top: 10px; margin-bottom: 10px; letter-spacing: 0.05em;">на {current_date}</h5>', unsafe_allow_html=True)

            st.markdown("---")

            district_row_data = region_row[cols_to_show].copy()
            dist_headers = list(cols_to_show)
            dist_header_html = "".join(f"<th>{c}</th>" for c in dist_headers)

            # --- ФОРМИРОВАНИЕ СТРОКИ НСО ---
            nso_row_html = ""
            if df_nso_summary is not None and not df_nso_summary.empty:
                nso_row = df_nso_summary.iloc[0] # Берем первую (единственную) строку
                nso_cells = ""
                for col in cols_to_show:
                    if col == "Район":
                        # Подменяем название на "Новосибирская область"
                        nso_cells += f'<td style="font-weight: 700 !important;">Новосибирская область</td>'
                        continue
                    
                    val = nso_row.get(col, "")
                    if str(col).startswith(("Уровень", "Изменение уровня")) and pd.notna(val):
                        try:
                            num_val = float(str(val).replace('%', '').replace(',', '.').strip())
                            if num_val <= 1.0: num_val *= 100
                            nso_cells += f'<td>{num_val:.1f}%</td>'
                        except:
                            nso_cells += f'<td>{val}</td>'
                    else:
                        nso_cells += f'<td>{val if pd.notna(val) else ""}</td>'
                
                # Добавляем стиль для визуального отделения строки области (светло-синий фон)
                nso_row_html = f'<tr style="background-color: #e8f4f8">{nso_cells}</tr>'

            # --- ФОРМИРОВАНИЕ СТРОКИ РАЙОНА ---
            dist_cells = ""
            for col in cols_to_show:
                val = district_row_data[col].values[0]
                if str(col).startswith(("Уровень", "Изменение уровня")) and pd.notna(val):
                    try:
                        num_val = float(str(val).replace('%', '').replace(',', '.').strip())
                        if num_val <= 1.0: num_val *= 100
                        dist_cells += f'<td>{num_val:.1f}%</td>'
                    except:
                        dist_cells += f'<td>{val}</td>'
                else:
                    dist_cells += f'<td>{val if pd.notna(val) else ""}</td>'

            # --- СБОРКА ИТОГОВОЙ ТАБЛИЦЫ ---
            district_table_html = f"""
            <table>
                <thead><tr>{dist_header_html}</tr></thead>
                <tbody>
                    {nso_row_html}
                    <tr>{dist_cells}</tr>
                </tbody>
            </table>
            """
            st.markdown(district_table_html, unsafe_allow_html=True)
            
            # --- ДОБАВЛЕНИЕ ТАБЛИЦЫ "УДЕЛЬНЫЕ ВЕСА" ---
            st.markdown("""
            <div style="display: flex; flex-direction: column; align-items: center; margin-top: 40px; margin-bottom: 40px;">
                <div style="font-family: var(--font-ui); font-size: 18px; font-weight: 600; margin-bottom: 15px; color: #1a252c;">
                    Вклад элементов созданной альтернативной инфраструктуры в показатель финансовой доступности
                </div>
                <div style="max-width: 900px; width: 100%;">
                    <table id="weights" class="weights-table" style="width: 100% !important;">
                        <thead>
                        <!-- Добавляем новую группирующую строку -->
                        <tr style="background-color: #f1f5f9;">
                            <th colspan="2">Классификация уровня финансовой доступности</th>
                            <th colspan="2">Вклад элементов альтернативной инфраструктуры</th>
                        </tr>
                            <tr>
                                <th>Уровень</th>
                                <th>Значение, %</th>
                                <th>Точка финансового доступа</th>
                                <th>Финансовый помощник</th>
                            </tr>
                        </thead>
                        <tbody>
                            <tr style="color: #27ae60; font-weight: 600;"><td>Хороший</td><td>86 – 100</td><td>0,5%</td><td>2%</td></tr>
                            <tr style="color: #2980b9; font-weight: 600;"><td>Выше среднего</td><td>66 – 85</td><td>1%</td><td>3%</td></tr>
                            <tr style="color: #d35400; font-weight: 600;"><td>Средний</td><td>46 – 65</td><td>2.5%</td><td>4%</td></tr>
                            <tr style="color: #c0392b; font-weight: 600;"><td>Ниже среднего</td><td>31 – 45</td><td>3%</td><td>5%</td></tr>
                            <tr style="color: #e74c3c; font-weight: 600;"><td>Недостаточный</td><td>0 – 30</td><td>4%</td><td>6%</td></tr>
                        </tbody>
                    </table>
                </div>
            </div>
            """, unsafe_allow_html=True)
            # --- "УДЕЛЬНЫЕ ВЕСА" ---

            st.markdown("<div style='margin-top: 20px;'></div>", unsafe_allow_html=True)
            st.markdown('<div class="district-section-title">Населенные пункты</div>', unsafe_allow_html=True)
            st.markdown('<div class="sort-caption">(работает сортировка по нажатию на заголовки)</div>', unsafe_allow_html=True)

            if not df_np_region.empty:
                np_headers_html = "".join(f"<th>{c}</th>" for c in available_cols)
                np_rows_html = ""
                for _, row in df_np_region.iterrows():
                    cells = ""
                    for col in available_cols:
                        val = row[col]
                        if pd.notna(val) and str(col).startswith(("Уровень", "Изменение уровня")):
                            try:
                                num_val = float(str(val).replace('%', '').replace(',', '.').strip())
                                if num_val <= 1.0: num_val = num_val * 100
                                cells += f'<td>{num_val:.1f}%</td>'
                            except (ValueError, TypeError):
                                cells += f'<td>{val}</td>'
                        else:
                            cells += f'<td>{val if pd.notna(val) else ""}</td>'
                    np_rows_html += f'<tr>{cells}</tr>\n'

                np_table_html = f"""
                <table id="npTable">
                    <thead><tr>{np_headers_html}</tr></thead>
                    <tbody>{np_rows_html}</tbody>
                </table>
                """
                st.markdown(np_table_html, unsafe_allow_html=True)
            else:
                if df_np_all is not None:
                    st.info("По данному району нет данных о населенных пунктах.")

            buttons_html = '<div class="buttons-container">'
            buttons_html += '<div class="btn-group-left">'
            if b64_tfd:
                buttons_html += f'<a class="portal-btn" href="data:application/zip;base64,{b64_tfd}" download="Как открыть ТФД.zip">📄Как открыть ТФД</a>'
            if b64_fp:
                buttons_html += f'<a class="portal-btn" href="data:application/zip;base64,{b64_fp}" download="Как назначить ФП.zip">📄Как назначить ФП</a>'
            buttons_html += '</div>'

            if not df_np_region.empty:
                buttons_html += f'<div class="btn-group-right"><a class="portal-btn" href="data:application/vnd.openxmlformats-officedocument.spreadsheetml.sheet;base64,{b64_np_excel}" download="NP_{region_name}.xlsx">📥 Выгрузить в Excel</a></div>'

            buttons_html += '</div>'
            st.markdown(buttons_html, unsafe_allow_html=True)

components.html(sorting_script, height=0)

st.markdown(
    f"""
    <div class="footer">
    🏦 Информационный портал финансовой доступности | 
    Разработано для органов государственной власти Новосибирской области |
    Посещений портала: <strong>{st.session_state.visit_count}</strong>
    </div>
    """,
    unsafe_allow_html=True
)