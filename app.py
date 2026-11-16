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

SHEET_URL = (
    "https://docs.google.com/spreadsheets/d/196icfDqkEMst54_Ze13CQ-lEBo47vCAKR7lGWFKbf6w/edit"
)
SHEET_CSV_URL = (
    "https://docs.google.com/spreadsheets/d/196icfDqkEMst54_Ze13CQ-lEBo47vCAKR7lGWFKbf6w/gviz/tq?tqx=out:csv&sheet=Activos"
)


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

    if (
        raw_name.startswith("0P")
        or raw_name == ticker_o_isin
        or ".F" in raw_name
    ):
        sufijo_cat = (
            "Fondo Indexado Global"
            if categoria == "Fondo Indexado"
            else categoria
        )
        nombre = f"{sufijo_cat} ({ticker_o_isin})"
    else:
        nombre = raw_name[:40]

    isin = info.get("isin") or (
        ticker_o_isin if len(ticker_o_isin) >= 10 else "IE-AUTO"
    )

    raw_ter = info.get("annualReportExpenseRatio") or info.get(
        "netExpenseRatio"
    )
    if raw_ter is not None:
        ter = (
            round(float(raw_ter) * 100, 2)
            if float(raw_ter) < 0.05
            else round(float(raw_ter), 2)
        )
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
        
