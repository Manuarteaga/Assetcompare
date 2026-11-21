from datetime import date
import json
import os
import re
from bs4 import BeautifulSoup
from google.oauth2.service_account import Credentials
import gspread
import pandas as pd
import requests
import streamlit as st
import yfinance as yf

st.set_page_config(
    page_title="AssetCompare Pro | Base de Datos en Drive",
    page_icon="📈",
    layout="wide",
)

st.markdown(
    """
    <style>
        .block-container { padding-top: 1rem; padding-bottom: 1rem; max-width: 100%; }
        header[data-testid="stHeader"] { display: none; }
    </style>
""",
    unsafe_allow_html=True,
)

SHEET_URL = "https://docs.google.com/spreadsheets/d/196icfDqkEMst54_Ze13CQ-lEBo47vCAKR7lGWFKbf6w/edit"
SHEET_CSV_URL = "https://docs.google.com/spreadsheets/d/196icfDqkEMst54_Ze13CQ-lEBo47vCAKR7lGWFKbf6w/gviz/tq?tqx=out:csv&sheet=Activos"

HEADERS_HTTP = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}


def get_gspread_client():
    if "gcp_service_account" not in st.secrets:
        return None
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"], scopes=scopes
    )
    return gspread.authorize(creds)


def cargar_desde_sheets():
    client = get_gspread_client()
    if client:
        sh = client.open_by_url(SHEET_URL)
        ws = sh.worksheet("Activos")
        records = ws.get_all_records()
        return pd.DataFrame(records), ws
    else:
        df = pd.read_csv(SHEET_CSV_URL)
        return df, None


def normalizar_ter(valor) -> float:
    """Convierte cualquier formato de TER (0.18, '0,18%', 18.0) a porcentaje limpio (ej. 0.18)."""
    if valor is None or valor == "":
        return 0.0
    try:
        num = float(str(valor).replace(",", ".").replace("%", "").strip())
        if num > 1.5:  # Si viene como 18.0 en lugar de 0.18
            num = num / 100.0
        elif 0 < num < 0.01:  # Si viene como decimal puro 0.0018
            num = num * 100.0
        return round(num, 2)
    except ValueError:
        return 0.0


def resolver_ticker_yahoo(query: str):
    """Convierte un ISIN (ej. IE00BYX5MX67) en su símbolo de Yahoo y obtiene su nombre comercial."""
    url = f"https://query2.finance.yahoo.com/v1/finance/search?q={query}&quotesCount=5"
    try:
        r = requests.get(url, headers=HEADERS_HTTP, timeout=6)
        data = r.json()
        quotes = data.get("quotes", [])
        if quotes:
            # Priorizar cotizaciones en EUR o bolsas europeas si hay varias
            elegido = quotes[0]
            for q in quotes:
                sym = q.get("symbol", "")
                if sym.endswith((".F", ".DE", ".MC", ".PA", ".AS")):
                    elegido = q
                    break
            nombre = elegido.get("longname") or elegido.get("shortname") or ""
            return elegido.get("symbol", query), nombre
    except Exception:
        pass
    return query, ""


def consultar_ft_markets(isin: str):
    """Respaldo para fondos indexados europeos por ISIN: extrae nombre, TER y rentabilidad 1Y/3Y/5Y."""
    if not re.match(r"^[A-Z]{2}[A-Z0-9]{10}$", isin):
        return {}
    for divisa in ["EUR", "USD"]:
        url = f"https://markets.ft.com/data/funds/tearsheet/summary?s={isin}:{divisa}"
        try:
            r = requests.get(url, headers=HEADERS_HTTP, timeout=7)
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            h1 = soup.find("h1", class_="mod-tearsheet-overview__header__name")
            if not h1:
                continue
            nombre = h1.get_text(strip=True)
            resultado = {"nombre": nombre}

            # Buscar gastos corrientes (Ongoing charge) y rentabilidades en tablas de resumen
            for row in soup.find_all("tr"):
                cells = [c.get_text(strip=True) for c in row.find_all(["th", "td"])]
                if len(cells) >= 2:
                    clave = cells[0].lower()
                    val = cells[1]
                    if "ongoing charge" in clave or "ter" in clave:
                        m = re.search(r"(\d+[.,]\d+)", val)
                        if m:
                            resultado["ter"] = normalizar_ter(m.group(1))
                    elif "5 year" in clave or "3 year" in clave or "1 year" in clave:
                        m = re.search(r"([+-]?\d+[.,]\d+%)", val)
                        if m and "rentabilidad" not in resultado:
                            etiqueta = "5a" if "5 year" in clave else ("3a" if "3 year" in clave else "1a")
                            resultado["rentabilidad"] = f"{m.group(1).replace(',', '.')} ({etiqueta})"
            return resultado
        except Exception:
            continue
    return {}


def consultar_activo(ticker_o_isin: str, categoria: str):
    entrada = ticker_o_isin.strip().upper()
    es_isin = bool(re.match(r"^[A-Z]{2}[A-Z0-9]{10}$", entrada))

    ticker_yahoo, nombre_search = resolver_ticker_yahoo(entrada)
    tk = yf.Ticker(ticker_yahoo)
    info = tk.info or {}

    isin = entrada if es_isin else (info.get("isin") or "")
    ft_data = consultar_ft_markets(isin) if isin else {}

    # 1. Nombre limpio (priorizando nombre comercial real sobre códigos 0P...)
    candidatos_nombre = [
        ft_data.get("nombre", ""),
        nombre_search,
        info.get("longName", ""),
        info.get("shortName", ""),
    ]
    nombre = ""
    for cand in candidatos_nombre:
        cand = str(cand).strip()
        if cand and not cand.startswith("0P") and cand != ticker_yahoo and ".F" not in cand:
            nombre = cand[:45]
            break
    if not nombre:
        nombre = f"{categoria} ({entrada})"

    # 2. TER normalizado
    raw_ter = info.get("annualReportExpenseRatio") or info.get("netExpenseRatio")
    if ft_data.get("ter") is not None:
        ter = ft_data["ter"]
    elif raw_ter is not None:
        ter = normalizar_ter(raw_ter)
    else:
        ter = 0.00 if categoria == "Acción" else (0.15 if categoria == "ETF" else 0.18)

    # 3. Rentabilidad anualizada (CAGR flexible: 5y -> 3y -> 1y -> FT Markets)
    ret_str = ""
    for periodo in ["5y", "3y", "1y"]:
        hist = tk.history(period=periodo)
        if not hist.empty and len(hist) >= 60:
            p_ini = float(hist["Close"].iloc[0])
            p_fin = float(hist["Close"].iloc[-1])
            if p_ini > 0:
                years = len(hist) / 252.0
                if years >= 0.9:
                    cagr = ((p_fin / p_ini) ** (1 / years) - 1) * 100
                    ret_str = f"{cagr:+.1f}% / año ({periodo})"
                else:
                    var = ((p_fin / p_ini) - 1) * 100
                    ret_str = f"{var:+.1f}% ({periodo})"
                break

    if not ret_str:
        if ft_data.get("rentabilidad"):
            ret_str = ft_data["rentabilidad"]
        else:
            div_yield = info.get("dividendYield") or info.get("yield")
            ret_str = f"TIR ~{float(div_yield)*100:.2f}%" if div_yield else "Sin histórico suficiente"

    divisa = info.get("currency", "EUR")
    replica = (
        f"Acción directa ({divisa})"
        if categoria == "Acción"
        else f"Acumulación/Índice ({divisa})"
    )
    fiscalidad = (
        "Traspasable (Sin peaje fiscal)"
        if categoria in ["Fondo Indexado", "Monetario"]
        else "Tributa al vender"
    )
    riesgo = info.get("category") or categoria

    return {
        "ticker_yahoo": ticker_yahoo,
        "nombre": nombre,
        "isin": isin or entrada,
        "ter": ter,
        "rentabilidad": ret_str,
        "replica": replica,
        "fiscalidad": fiscalidad,
        "riesgo": riesgo,
    }


def autocompletar_filas_incompletas(worksheet):
    if not worksheet:
        return False
    filas = worksheet.get_all_values()
    hubo_cambios = False
    hoy = date.today().isoformat()

    for i, fila in enumerate(filas[1:], start=2):
        while len(fila) < 12:
            fila.append("")

        ticker_o_isin = fila[1].strip().upper()
        isin_col = fila[2].strip().upper()
        nombre_actual = fila[3].strip()
        categoria = fila[4].strip() or "Fondo Indexado"
        ter_actual = fila[5].strip()
        ret_actual = fila[6].strip()

        busqueda = ticker_o_isin or isin_col
        falta_nombre = not nombre_actual or nombre_actual == "Pendiente Yahoo" or nombre_actual.startswith("0P")
        falta_datos = not ter_actual or not ret_actual or ret_actual == "Sincronizado Yahoo"

        if busqueda and (falta_nombre or falta_datos):
            try:
                datos = consultar_activo(busqueda, categoria)
                prod_id = "gs_" + busqueda.lower().replace(".", "_").replace("-", "_")
                fila_completa = [
                    prod_id,
                    datos["ticker_yahoo"],
                    datos["isin"],
                    datos["nombre"] if falta_nombre else nombre_actual,
                    categoria,
                    datos["ter"] if not ter_actual else normalizar_ter(ter_actual),
                    datos["rentabilidad"] if (not ret_actual or ret_actual == "Sincronizado Yahoo") else ret_actual,
                    datos["replica"],
                    datos["fiscalidad"],
                    datos["riesgo"],
                    f'=IFERROR(GOOGLEFINANCE("{datos["ticker_yahoo"]}","price"), "Manual/Yahoo")',
                    hoy,
                ]
                worksheet.update(
                    f"A{i}:L{i}",
                    [fila_completa],
                    value_input_option="USER_ENTERED",
                )
                hubo_cambios = True
            except Exception:
                pass
    return hubo_cambios


try:
    df_activos, worksheet = cargar_desde_sheets()
    if worksheet and autocompletar_filas_incompletas(worksheet):
        df_activos, _ = cargar_desde_sheets()
        st.toast("✨ Fondos actualizados automáticamente con nombre limpio, TER y rentabilidad.")
except Exception as e:
    st.error(f"Error al conectar con Google Sheets: {e}")
    st.stop()

col1, col2, col3, col4 = st.columns([3.2, 1.8, 1.8, 1.6])
with col1:
    nuevo_ticker = (
        st.text_input(
            "Buscar Ticker o ISIN",
            placeholder="Añadir ISIN o Ticker (ej. IE00BYX5MX67, IE00B03HD191, VWCE.DE)...",
            label_visibility="collapsed",
        )
        .strip()
        .upper()
    )
with col2:
    nueva_cat = st.selectbox(
        "Categoría",
        ["Fondo Indexado", "ETF", "Monetario", "Acción"],
        label_visibility="collapsed",
    )
with col3:
    btn_agregar = st.button(
        "➕ Buscar y Guardar en Drive",
        use_container_width=True,
        type="primary",
    )
with col4:
    btn_sync = st.button(
        "🔄 Actualizar Datos",
        use_container_width=True,
    )

if btn_agregar and nuevo_ticker:
    with st.spinner(f"Buscando datos oficiales para {nuevo_ticker}..."):
        datos = consultar_activo(nuevo_ticker, nueva_cat)
        prod_id = "gs_" + nuevo_ticker.lower().replace(".", "_").replace("-", "_")
        hoy = date.today().isoformat()

        nueva_fila = [
            prod_id,
            datos["ticker_yahoo"],
            datos["isin"],
            datos["nombre"],
            nueva_cat,
            datos["ter"],
            datos["rentabilidad"],
            datos["replica"],
            datos["fiscalidad"],
            datos["riesgo"],
            f'=IFERROR(GOOGLEFINANCE("{datos["ticker_yahoo"]}","price"), "Manual/Yahoo")',
            hoy,
        ]

        if worksheet:
            existentes_b = [str(x).upper() for x in worksheet.col_values(2)]
            existentes_c = [str(x).upper() for x in worksheet.col_values(3)]
            if nuevo_ticker in existentes_b or nuevo_ticker in existentes_c:
                idx = (
                    existentes_b.index(nuevo_ticker) + 1
                    if nuevo_ticker in existentes_b
                    else existentes_c.index(nuevo_ticker) + 1
                )
                worksheet.update(
                    f"B{idx}:L{idx}",
                    [nueva_fila[1:]],
                    value_input_option="USER_ENTERED",
                )
                st.success(f"🔄 **{datos['nombre']}** actualizado en tu Google Sheet.")
            else:
                col_b = worksheet.col_values(2)
                primera_libre = len(col_b) + 1
                worksheet.update(
                    f"A{primera_libre}:L{primera_libre}",
                    [nueva_fila],
                    value_input_option="USER_ENTERED",
                )
                st.success(f"✅ **{datos['nombre']}** guardado en la fila {primera_libre}.")
            df_activos, _ = cargar_desde_sheets()
        else:
            st.warning("Configura `[gcp_service_account]` en Streamlit Secrets para guardar en Drive.")

if btn_sync and worksheet:
    with st.spinner("Sincronizando fondos y reparando nombres/TER/rentabilidades..."):
        autocompletar_filas_incompletas(worksheet)
        df_activos, _ = cargar_desde_sheets()
        st.success("✅ Base de datos actualizada.")

badge_map = {
    "Fondo Indexado": "text-sky-400",
    "ETF": "text-indigo-400",
    "Monetario": "text-emerald-400",
    "Acción": "text-amber-400",
}

products_dict = {}
for _, row in df_activos.iterrows():
    pid = str(row.get("ID", "")).strip()
    nombre_row = str(row.get("Nombre", "")).strip()
    if not pid or not nombre_row:
        continue
    cat = str(row.get("Categoria", "Fondo Indexado")).strip()
    ter_num = normalizar_ter(row.get("TER_Pct", 0))

    products_dict[pid] = {
        "type": cat,
        "badgeColor": badge_map.get(cat, "text-sky-400"),
        "name": nombre_row,
        "isin": str(row.get("ISIN", "")),
        "ter": ter_num,
        "return10y": str(row.get("Rentabilidad_Anual", "")),
        "replication": str(row.get("Replica_o_Dividendo", "")),
        "fiscality": str(row.get("Fiscalidad", "")),
        "risk": str(row.get("Riesgo", "")),
    }

html_path = os.path.join(os.path.dirname(__file__), "index.html")
with open(html_path, "r", encoding="utf-8") as f:
    html_code = f.read()

products_json = json.dumps(products_dict, ensure_ascii=False)
injection_script = f"""
<script>
    const SHEET_PRODUCTS = {products_json};
    Object.assign(PRODUCTS, SHEET_PRODUCTS);

    window.addEventListener('DOMContentLoaded', () => {{
        const catSelects = {{
            'Fondo Indexado': ['sel-fondo-a', 'sel-fondo-b'],
            'ETF': ['sel-etf-a', 'sel-etf-b'],
            'Monetario': ['sel-monetary-a', 'sel-monetary-b'],
            'Acción': ['sel-stock-a', 'sel-stock-b']
        }};

        Object.values(catSelects).flat().forEach(id => {{
            const el = document.getElementById(id);
            if (el) el.innerHTML = '';
        }});

        for (const [key, prod] of Object.entries(SHEET_PRODUCTS)) {{
            const targets = catSelects[prod.type] || [];
            targets.forEach(selId => {{
                const sel = document.getElementById(selId);
                if (sel) {{
                    const opt = document.createElement('option');
                    opt.value = key;
                    opt.text = prod.name + " (" + prod.isin + ")";
                    sel.add(opt);
                }}
            }});
        }}

        Object.values(catSelects).forEach(([idA, idB]) => {{
            const selA = document.getElementById(idA);
            const selB = document.getElementById(idB);
            if (selA && selA.options.length > 0) selA.selectedIndex = 0;
            if (selB && selB.options.length > 1) selB.selectedIndex = 1;
        }});

        updateComparison();
    }});
</script>
</body>
"""

html_final = html_code.replace("</body>", injection_script)
st.components.v1.html(html_final, height=960, scrolling=True)
