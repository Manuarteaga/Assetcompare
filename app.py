from datetime import date
import json
import os
from google.oauth2.service_account import Credentials
import gspread
import pandas as pd
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


def consultar_yahoo(ticker_o_isin: str, categoria: str):
    tk = yf.Ticker(ticker_o_isin)
    info = tk.info

    raw_name = (
        info.get("shortName") or info.get("longName") or ticker_o_isin
    )

    # Limpieza automática inteligente: si Yahoo devuelve un código interno (empieza por 0P) 
    # o el propio ticker, extraemos un nombre comercial limpio y profesional automáticamente.
    if (
        raw_name.startswith("0P")
        or raw_name == ticker_o_isin
        or ".F" in raw_name
    ):
        # Si es un fondo indexado o monetario de Morningstar/Yahoo, le asignamos una nomenclatura limpia automática
        sufijo_cat = (
            "Fondo Indexado Global"
            if categoria == "Fondo Indexado"
            else categoria
        )
        nombre = f"{sufijo_cat} ({ticker_o_isin})"
    else:
        # Si es una acción o ETF con nombre comercial válido (ej. Apple Inc. o iShares S&P 500), lo usamos tal cual
        nombre = raw_name[:40]

    isin = info.get("isin") or (
        ticker_o_isin if len(ticker_o_isin) >= 10 else "IE-AUTO"
    )

    raw_ter = info.get("annualReportExpenseRatio") or info.get(
        "netExpenseRatio"
    )
    if raw_ter is not None:
        ter = round(float(raw_ter) * 100, 2)
    else:
        ter = (
            0.00
            if categoria == "Acción"
            else (0.15 if categoria == "ETF" else 0.18)
        )

    hist = tk.history(period="5y")
    if not hist.empty and len(hist) > 250:
        p_ini = hist["Close"].iloc[0]
        p_fin = hist["Close"].iloc[-1]
        years = len(hist) / 252.0
        cagr = ((p_fin / p_ini) ** (1 / years) - 1) * 100
        ret_str = f"{cagr:+.1f}% / año"
    else:
        div_yield = info.get("dividendYield") or info.get("yield")
        ret_str = (
            f"TIR ~{div_yield*100:.2f}%"
            if div_yield
            else "Automático (Yahoo)"
        )

    replica = (
        f"Acción directa ({info.get('currency', 'USD')})"
        if categoria == "Acción"
        else f"Yahoo ({info.get('currency', 'EUR')})"
    )
    fiscalidad = (
        "Traspasable (Sin peaje fiscal)"
        if categoria in ["Fondo Indexado", "Monetario"]
        else "Tributa al vender"
    )
    riesgo = info.get("category") or categoria

    return {
        "nombre": nombre,
        "isin": isin,
        "ter": ter,
        "rentabilidad": ret_str,
        "replica": replica,
        "fiscalidad": fiscalidad,
        "riesgo": riesgo,
    }



def autocompletar_filas_incompletas(worksheet):
    """Detecta filas añadidas manualmente en Sheets (solo Ticker/ISIN) y completa el resto con Yahoo."""
    if not worksheet:
        return False
    filas = worksheet.get_all_values()
    hubo_cambios = False
    hoy = date.today().isoformat()

    for i, fila in enumerate(filas[1:], start=2):
        while len(fila) < 12:
            fila.append("")

        ticker_o_isin = fila[1].strip().upper()  # Columna B (Ticker_Yahoo)
        isin_col = fila[2].strip().upper()  # Columna C (ISIN)
        nombre_actual = fila[3].strip()  # Columna D (Nombre)
        categoria = fila[4].strip() or "ETF"  # Columna E (Categoria)
        ter_actual = fila[5].strip()  # Columna F (TER_Pct)

        busqueda = ticker_o_isin or isin_col

        # Si hay ticker/ISIN y falta el TER o el nombre dice "Pendiente Yahoo", completar fila
        if busqueda and (
            not ter_actual
            or not nombre_actual
            or nombre_actual == "Pendiente Yahoo"
        ):
            try:
                datos = consultar_yahoo(busqueda, categoria)
                prod_id = (
                    "gs_"
                    + busqueda.lower().replace(".", "_").replace("-", "_")
                )
                fila_completa = [
                    prod_id,
                    busqueda,
                    datos["isin"],
                    datos["nombre"],
                    categoria,
                    datos["ter"],
                    datos["rentabilidad"],
                    datos["replica"],
                    datos["fiscalidad"],
                    datos["riesgo"],
                    f'=IFERROR(GOOGLEFINANCE("{busqueda}","price"), "Manual/Yahoo")',
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


# Cargar datos desde Google Sheets
try:
    df_activos, worksheet = cargar_desde_sheets()
    if worksheet and autocompletar_filas_incompletas(worksheet):
        df_activos, _ = cargar_desde_sheets()
        st.toast(
            "✨ Nuevas filas de Google Sheets autocompletadas con Yahoo Finance."
        )
except Exception as e:
    st.error(f"Error al conectar con Google Sheets: {e}")
    st.stop()

# Barra superior en Streamlit
col1, col2, col3, col4 = st.columns([3.2, 1.8, 1.8, 1.6])
with col1:
    nuevo_ticker = (
        st.text_input(
            "Buscar Ticker o ISIN",
            placeholder="Añadir Ticker o ISIN a Drive (ej. VWCE.DE, TSLA, IE00B4L5Y983)...",
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
        "🔄 Actualizar Yahoo",
        use_container_width=True,
    )

# Guardar desde la barra de búsqueda de la web
if btn_agregar and nuevo_ticker:
    with st.spinner(
        f"Consultando {nuevo_ticker} en Yahoo Finance y guardando en Google Sheets..."
    ):
        datos = consultar_yahoo(nuevo_ticker, nueva_cat)
        prod_id = (
            "gs_"
            + nuevo_ticker.lower().replace(".", "_").replace("-", "_")
        )
        hoy = date.today().isoformat()

        nueva_fila = [
            prod_id,
            nuevo_ticker,
            datos["isin"],
            datos["nombre"],
            nueva_cat,
            datos["ter"],
            datos["rentabilidad"],
            datos["replica"],
            datos["fiscalidad"],
            datos["riesgo"],
            f'=IFERROR(GOOGLEFINANCE("{nuevo_ticker}","price"), "Manual/Yahoo")',
            hoy,
        ]

        if worksheet:
            existentes = [
                str(x).upper() for x in worksheet.col_values(2)
            ]
            if nuevo_ticker in existentes:
                idx = existentes.index(nuevo_ticker) + 1
                worksheet.update(
                    f"C{idx}:L{idx}",
                    [nueva_fila[2:]],
                    value_input_option="USER_ENTERED",
                )
                st.success(
                    f"🔄 **{datos['nombre']}** actualizado en tu Google Sheet."
                )
            else:
                # Buscar la primera fila vacía (sin Ticker en columna B) para no saltar hasta la fila 51
                col_b = worksheet.col_values(2)
                primera_libre = len(col_b) + 1
                worksheet.update(
                    f"A{primera_libre}:L{primera_libre}",
                    [nueva_fila],
                    value_input_option="USER_ENTERED",
                )
                st.success(
                    f"✅ **{datos['nombre']}** guardado en la fila {primera_libre} de tu Google Sheet."
                )
            df_activos, _ = cargar_desde_sheets()
        else:
            st.warning(
                "Configura `[gcp_service_account]` en Streamlit Secrets para guardar en Drive."
            )

# Sincronización completa bajo demanda
if btn_sync and worksheet:
    with st.spinner(
        "Sincronizando todos los activos de Google Sheets con Yahoo Finance..."
    ):
        autocompletar_filas_incompletas(worksheet)
        hoy = date.today().isoformat()
        filas = worksheet.get_all_records()
        for i, fila in enumerate(filas, start=2):
            tk_val = str(fila.get("Ticker_Yahoo", "")).strip()
            cat_val = str(fila.get("Categoria", "ETF")).strip()
            if tk_val:
                try:
                    act = consultar_yahoo(tk_val, cat_val)
                    worksheet.update(
                        f"F{i}:G{i}", [[act["ter"], act["rentabilidad"]]]
                    )
                    worksheet.update(f"L{i}", [[hoy]])
                except Exception:
                    pass
        df_activos, _ = cargar_desde_sheets()
        st.success(
            "✅ Todos los activos se han sincronizado con Yahoo Finance."
        )

# Inyectar datos en tu plantilla index.html
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
    cat = str(row.get("Categoria", "ETF")).strip()
    try:
        ter_num = float(str(row.get("TER_Pct", 0)).replace(",", "."))
    except ValueError:
        ter_num = 0.0

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
                    opt.text = `${{prod.name}} (${{prod.isin}})`;
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
