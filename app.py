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

# URL de tu hoja creada en Google Drive
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
    """Lee todos los activos desde Google Sheets (por API si hay credenciales, o por CSV público)."""
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
    """Obtiene TER, rentabilidad y nombre actualizado desde Yahoo Finance."""
    tk = yf.Ticker(ticker_o_isin)
    info = tk.info
    nombre = (
        info.get("shortName") or info.get("longName") or ticker_o_isin
    )[:36]
    isin = info.get("isin") or ticker_o_isin

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
            else "Sincronizado Yahoo"
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


# Cargar base de datos desde Drive
try:
    df_activos, worksheet = cargar_desde_sheets()
except Exception as e:
    st.error(
        f"Asegúrate de compartir la hoja como pública (Lector) o configurar los Secrets de Google. Detalle: {e}"
    )
    st.stop()

# Barra superior de búsqueda y sincronización con Google Sheets
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

# Acción 1: Buscar un producto nuevo en Yahoo y añadir la fila a Google Sheets
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
            # Evitar duplicados si ya existe el ticker
            existentes = [
                str(x).upper() for x in worksheet.col_values(2)
            ]
            if nuevo_ticker in existentes:
                idx = existentes.index(nuevo_ticker) + 1
                worksheet.update(
                    f"C{idx}:L{idx}", [nueva_fila[2:]]
                )
                st.success(
                    f"🔄 **{datos['nombre']}** ya existía en tu Google Sheet y se ha actualizado."
                )
            else:
                worksheet.append_row(
                    nueva_fila, value_input_option="USER_ENTERED"
                )
                st.success(
                    f"✅ **{datos['nombre']}** guardado permanentemente en tu hoja de Google Drive."
                )
            df_activos, _ = cargar_desde_sheets()
        else:
            # Si aún no están configurados los Secrets de escritura, lo añade temporalmente y avisa
            st.warning(
                "Configura `[gcp_service_account]` en Streamlit Secrets para guardar nuevas filas en Drive automáticamente. Cargado en la sesión actual."
            )
            nueva_df = pd.DataFrame(
                [nueva_fila], columns=df_activos.columns
            )
            df_activos = pd.concat(
                [df_activos, nueva_df], ignore_index=True
            )

# Acción 2: Sincronizar toda la tabla con los últimos datos de Yahoo Finance
if btn_sync:
    if not worksheet:
        st.warning(
            "Para sobrescribir actualizaciones en Google Sheets necesitas configurar `[gcp_service_account]` en Streamlit Secrets."
        )
    else:
        with st.spinner(
            "Sincronizando todos los activos de Google Sheets con Yahoo Finance..."
        ):
            hoy = date.today().isoformat()
            filas = worksheet.get_all_records()
            for i, fila in enumerate(filas, start=2):
                tk_val = str(fila.get("Ticker_Yahoo", "")).strip()
                cat_val = str(fila.get("Categoria", "ETF")).strip()
                if tk_val:
                    try:
                        act = consultar_yahoo(tk_val, cat_val)
                        worksheet.update(
                            f"F{i}:G{i}",
                            [[act["ter"], act["rentabilidad"]]],
                        )
                        worksheet.update(f"L{i}", [[hoy]])
                    except Exception:
                        pass
            df_activos, _ = cargar_desde_sheets()
            st.success(
                "✅ Todos los TER y rentabilidades de tu Google Sheet se han sincronizado con Yahoo Finance."
            )

# Convertir el DataFrame de Google Sheets al diccionario PRODUCTS de tu plantilla HTML
badge_map = {
    "Fondo Indexado": "text-sky-400",
    "ETF": "text-indigo-400",
    "Monetario": "text-emerald-400",
    "Acción": "text-amber-400",
}

products_dict = {}
for _, row in df_activos.iterrows():
    pid = str(row.get("ID", "")).strip()
    if not pid:
        continue
    cat = str(row.get("Categoria", "ETF")).strip()
    try:
        ter_num = float(str(row.get("TER_Pct", 0)).replace(",", "."))
    except ValueError:
        ter_num = 0.0

    products_dict[pid] = {
        "type": cat,
        "badgeColor": badge_map.get(cat, "text-sky-400"),
        "name": str(row.get("Nombre", pid)),
        "isin": str(row.get("ISIN", "")),
        "ter": ter_num,
        "return10y": str(row.get("Rentabilidad_Anual", "")),
        "replication": str(row.get("Replica_o_Dividendo", "")),
        "fiscality": str(row.get("Fiscalidad", "")),
        "risk": str(row.get("Riesgo", "")),
    }

# Leer tu index.html e inyectar dinámicamente los datos leídos de Google Sheets
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

        // Limpiar y repoblar desplegables directamente desde Google Sheets
        Object.values(catSelects).flat().forEach(id => {{
            const el = document.getElementById(id);
            if (el) el.innerHTML = '';
        }});

        for (const [key, prod] of Object.entries(SHEET_PRODUCTS)) {{
            const targets = catSelects[prod.type] || [];
            targets.forEach((selId, idx) => {{
                const sel = document.getElementById(selId);
                if (sel) {{
                    const opt = document.createElement('option');
                    opt.value = key;
                    opt.text = `${{prod.name}} (${{prod.isin}})`;
                    sel.add(opt);
                }}
            }});
        }}

        // Seleccionar por defecto el primer elemento en A y el segundo en B
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
