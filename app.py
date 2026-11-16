import json
import streamlit as st
import yfinance as yf

st.set_page_config(
    page_title="AssetCompare Pro | Comparador A vs B",
    page_icon="📈",
    layout="wide",
)

# Ocultar márgenes superiores de Streamlit para que luzca a pantalla completa
st.markdown(
    """
    <style>
        .block-container { padding-top: 1rem; padding-bottom: 1rem; max-width: 100%; }
        header[data-testid="stHeader"] { display: none; }
    </style>
""",
    unsafe_allow_html=True,
)

# Inicializar memoria de productos personalizados en la sesión
if "custom_products" not in st.session_state:
    st.session_state.custom_products = {}

# Barra superior en Streamlit para consultar Yahoo Finance en tiempo real
with st.container():
    col1, col2, col3, col4 = st.columns([3, 2, 2, 1.5])
    with col1:
        ticker_query = (
            st.text_input(
                "🔍 Buscar Ticker o ISIN en Yahoo Finance",
                placeholder="Ej: TSLA, SPY, VWCE.DE, IE00B4L5Y983...",
                label_visibility="collapsed",
            )
            .strip()
            .upper()
        )
    with col2:
        cat_query = st.selectbox(
            "Categoría",
            ["ETF", "Fondo Indexado", "Acción", "Monetario"],
            label_visibility="collapsed",
        )
    with col3:
        slot_query = st.selectbox(
            "Asignar a",
            ["Añadir a listas (A y B)", "Fijar en Opción A", "Fijar en Opción B"],
            label_visibility="collapsed",
        )
    with col4:
        buscar_btn = st.button(
            "➕ Cargar de Yahoo", use_container_width=True, type="primary"
        )

if buscar_btn and ticker_query:
    with st.spinner(
        f"Consultando datos de {ticker_query} en Yahoo Finance / Morningstar..."
    ):
        try:
            tk = yf.Ticker(ticker_query)
            info = tk.info

            nombre = (
                info.get("shortName") or info.get("longName") or ticker_query
            )
            isin = info.get("isin") or ticker_query

            # Extraer TER (annualReportExpenseRatio en yfinance viene en decimal, ej. 0.0007 -> 0.07%)
            raw_ter = info.get("annualReportExpenseRatio") or info.get(
                "netExpenseRatio"
            )
            ter = (
                round(float(raw_ter) * 100, 2)
                if raw_ter
                else (
                    0.00
                    if cat_query == "Acción"
                    else (0.15 if cat_query == "ETF" else 0.18)
                )
            )

            # Calcular rentabilidad anualizada (CAGR) a 5-10 años si hay histórico
            hist = tk.history(period="5y")
            if not hist.empty and len(hist) > 250:
                p_ini = hist["Close"].iloc[0]
                p_fin = hist["Close"].iloc[-1]
                years = len(hist) / 252.0
                cagr = ((p_fin / p_ini) ** (1 / years) - 1) * 100
                ret_str = f"{cagr:+.1f}% / año (5a)"
            else:
                div_yield = info.get("dividendYield") or info.get("yield")
                ret_str = (
                    f"Yield ~{div_yield*100:.2f}%"
                    if div_yield
                    else "Datos recientes"
                )

            badge_map = {
                "Fondo Indexado": "text-sky-400",
                "ETF": "text-indigo-400",
                "Monetario": "text-emerald-400",
                "Acción": "text-amber-400",
            }
            fisc_map = {
                "Fondo Indexado": "Traspasable (Sin peaje fiscal)",
                "Monetario": "Traspasable (Sin peaje fiscal)",
                "ETF": "Tributa al vender",
                "Acción": "Tributa al vender",
            }

            key = "yf_" + ticker_query.lower().replace(".", "_").replace(
                "-", "_"
            )
            st.session_state.custom_products[key] = {
                "type": cat_query,
                "badgeColor": badge_map[cat_query],
                "name": nombre[:32],
                "isin": isin,
                "ter": ter,
                "return10y": ret_str,
                "replication": f"Yahoo ({info.get('currency', 'USD')})",
                "fiscality": fisc_map[cat_query],
                "risk": info.get("category") or cat_query,
                "slot_target": slot_query,
            }
            st.success(
                f"✅ **{nombre}** cargado con éxito (TER: {ter}% | Rentabilidad: {ret_str})"
            )
        except Exception as e:
            st.warning(
                f"No se encontraron todos los datos en Yahoo para '{ticker_query}'. Prueba con el ticker exacto de Yahoo (ej. VWCE.DE, IWDA.AS, AAPL)."
            )

# Leer tu plantilla HTML base e inyectar los productos personalizados obtenidos de Yahoo
with open("index.html", "r", encoding="utf-8") as f:
    html_code = f.read()

custom_json = json.dumps(st.session_state.custom_products)
injection_script = f"""
<script>
    const CUSTOM_FROM_PYTHON = {custom_json};
    window.addEventListener('DOMContentLoaded', () => {{
        const catMap = {{
            'Fondo Indexado': ['sel-fondo-a', 'sel-fondo-b'],
            'ETF': ['sel-etf-a', 'sel-etf-b'],
            'Monetario': ['sel-monetary-a', 'sel-monetary-b'],
            'Acción': ['sel-stock-a', 'sel-stock-b']
        }};
        for (const [key, prod] of Object.entries(CUSTOM_FROM_PYTHON)) {{
            PRODUCTS[key] = prod;
            const targets = catMap[prod.type] || [];
            targets.forEach((selId, idx) => {{
                const sel = document.getElementById(selId);
                if (sel) {{
                    const opt = document.createElement('option');
                    opt.value = key;
                    opt.text = `${{prod.name}} (${{prod.isin}}) [Yahoo]`;
                    sel.add(opt);
                    if (prod.slot_target === 'Fijar en Opción A' && idx === 0) sel.value = key;
                    if (prod.slot_target === 'Fijar en Opción B' && idx === 1) sel.value = key;
                    if (prod.slot_target === 'Añadir a listas (A y B)' && idx === 1) sel.value = key;
                }}
            }});
        }}
        updateComparison();
    }});
</script>
</body>
"""

html_final = html_code.replace("</body>", injection_script)
st.components.v1.html(html_final, height=960, scrolling=True)
