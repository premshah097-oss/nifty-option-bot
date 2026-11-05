import os
import json
import pyotp
import requests
import pandas as pd
import streamlit as st
from SmartApi import SmartConnect

# ==========================================
# 🔐 APNI ANGEL ONE DETAILS YAHAN BHARO
# ==========================================
API_KEY = "YOUR_API_KEY_HERE"
CLIENT_ID = "YOUR_CLIENT_ID_HERE"
MPIN = "YOUR_4_DIGIT_MPIN"
TOTP_SECRET = "YOUR_TOTP_STRING_HERE"

STARTING_CAPITAL = 25000.0
CSV_FILE = "trades_history.csv"
OPEN_POS_FILE = "open_position.json"

st.set_page_config(page_title="Nifty OTM Pro Dashboard", page_icon="📊", layout="wide")
st.title("📊 Nifty OTM Option Bot — 30-Day P&L & Live Radar")

# Load Trade History Database
if os.path.exists(CSV_FILE):
    df_trades = pd.read_csv(CSV_FILE)
else:
    df_trades = pd.DataFrame(columns=[
        "Date", "Entry_Time", "Exit_Time", "Signal", "Contract",
        "Entry_Price", "Exit_Price", "Gross_PnL", "Brokerage", "Net_PnL", "Exit_Reason"
    ])

# Calculate 30-Day & Lifetime Portfolio Metrics
if not df_trades.empty:
    df_trades["Date_dt"] = pd.to_datetime(df_trades["Date"])
    cutoff_30d = pd.Timestamp.today() - pd.Timedelta(days=30)
    df_30d = df_trades[df_trades["Date_dt"] >= cutoff_30d].copy()

    total_net_pnl = df_trades["Net_PnL"].sum()
    pnl_30d = df_30d["Net_PnL"].sum()
    total_trades_30d = len(df_30d)
    wins_30d = len(df_30d[df_30d["Net_PnL"] > 0])
    win_rate = round((wins_30d / total_trades_30d) * 100, 1) if total_trades_30d > 0 else 0.0
    total_brokerage = df_30d["Brokerage"].sum()
    best_trade = df_30d["Net_PnL"].max() if total_trades_30d > 0 else 0.0
    worst_trade = df_30d["Net_PnL"].min() if total_trades_30d > 0 else 0.0
else:
    total_net_pnl, pnl_30d, total_trades_30d, win_rate, total_brokerage, best_trade, worst_trade = 0.0, 0.0, 0, 0.0, 0.0, 0.0, 0.0

current_wallet = STARTING_CAPITAL + total_net_pnl

# Top Summary Cards
c1, c2, c3, c4 = st.columns(4)
c1.metric("💰 Virtual Wallet Balance", f"₹{current_wallet:,.2f}", f"₹{total_net_pnl:+,.2f} Lifetime")
c2.metric("📅 Last 30 Days Net P&L", f"₹{pnl_30d:+,.2f}", f"{total_trades_30d} Trades")
c3.metric("🎯 30-Day Win Rate", f"{win_rate}%", f"Best: ₹{best_trade:+.0f} | Worst: ₹{worst_trade:+.0f}")
c4.metric("🧾 Brokerage Deducted (30d)", f"₹{total_brokerage:,.0f}", "₹60 per trade")

# Show Open Carried Trade if any
if os.path.exists(OPEN_POS_FILE):
    try:
        with open(OPEN_POS_FILE, "r") as f:
            open_pos = json.load(f)
        if open_pos:
            st.info(f"🔥 **ACTIVE / CARRIED POSITION:** {open_pos['symbol']} | Entry: ₹{open_pos['entry']} | SL: ₹{open_pos['sl']} | Target: ₹{open_pos['target']}")
    except Exception:
        pass

st.divider()

tab1, tab2, tab3 = st.tabs(["📈 30-Day P&L Analytics", "⚡ Live Option Chain & Spot", "📜 Trade History Log"])

with tab1:
    if not df_trades.empty:
        col_a, col_b = st.columns(2)
        with col_a:
            st.subheader("📈 Wallet Growth Curve")
            df_trades["Cumulative_Wallet"] = STARTING_CAPITAL + df_trades["Net_PnL"].cumsum()
            st.line_chart(df_trades.set_index("Exit_Time")["Cumulative_Wallet"])
        with col_b:
            st.subheader("📊 Daily Net P&L (Last 30 Days)")
            daily_summary = df_30d.groupby("Date")["Net_PnL"].sum().reset_index()
            st.bar_chart(daily_summary.set_index("Date")["Net_PnL"])
    else:
        st.info("📭 Abhi tak koi trade close nahi hua hai. Pehla paper trade close hote hi yahan 30-Day P&L Graph aur Win-Rate dikhega!")

with tab2:
    st.subheader("📡 Live Nifty 50 Spot & Near-ATM Option Chain")
    if st.button("🔄 Refresh Live Market Data"):
        st.rerun()
    try:
        smartApi = SmartConnect(api_key=API_KEY)
        totp = pyotp.TOTP(TOTP_SECRET).now()
        session = smartApi.generateSession(CLIENT_ID, MPIN, totp)
        if session['status']:
            spot_res = smartApi.ltpData("NSE", "Nifty 50", "99926000")
            spot_ltp = float(spot_res['data']['ltp'])
            atm_strike = round(spot_ltp / 50) * 50

            url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
            df_scrip = pd.DataFrame(requests.get(url).json())
            nifty_opts = df_scrip[(df_scrip['name'] == 'NIFTY') & (df_scrip['instrumenttype'] == 'OPTIDX')].copy()
            nifty_opts['expiry'] = pd.to_datetime(nifty_opts['expiry'], format='%d%b%Y')
            nearest_exp = nifty_opts[nifty_opts['expiry'] >= pd.Timestamp.today().normalize()]['expiry'].min()
            weekly_df = nifty_opts[nifty_opts['expiry'] == nearest_exp].copy()
            weekly_df['strike_price'] = weekly_df['strike'].astype(float) / 100.0

            strikes = [atm_strike - 100, atm_strike - 50, atm_strike, atm_strike + 50, atm_strike + 100]
            sub_df = weekly_df[weekly_df['strike_price'].isin(strikes)].copy()
            mkt_data = smartApi.getMarketData("FULL", {"NFO": sub_df['token'].tolist()})['data']['fetched']
            token_map = {item['symbolToken']: item for item in mkt_data}

            rows, tot_ce, tot_pe = [], 0, 0
            for s in strikes:
                ce_r = sub_df[(sub_df['strike_price'] == s) & (sub_df['symbol'].str.endswith('CE'))].iloc[0]
                pe_r = sub_df[(sub_df['strike_price'] == s) & (sub_df['symbol'].str.endswith('PE'))].iloc[0]
                c_oi = int(token_map.get(ce_r['token'], {}).get('opnInterest', 0))
                p_oi = int(token_map.get(pe_r['token'], {}).get('opnInterest', 0))
                c_ltp = float(token_map.get(ce_r['token'], {}).get('ltp', 0))
                p_ltp = float(token_map.get(pe_r['token'], {}).get('ltp', 0))
                tot_ce += c_oi
                tot_pe += p_oi
                rows.append({
                    "Call OI": f"{c_oi:,}", "Call LTP (₹)": c_ltp,
                    "STRIKE": f"👉 {int(s)}" if s == atm_strike else str(int(s)),
                    "Put LTP (₹)": p_ltp, "Put OI": f"{p_oi:,}"
                })
            pcr = round(tot_pe / tot_ce, 2) if tot_ce > 0 else 1.0
            st.write(f"**Nifty 50 Spot:** `₹{spot_ltp:,.2f}` | **ATM Strike:** `{int(atm_strike)}` | **Live PCR:** `{pcr}`")
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    except Exception as e:
        st.warning(f"Live feed abhi connect nahi ho paya: {e}")

with tab3:
    st.subheader("📜 Complete Paper Trade Log (Last 30 Days & All-Time)")
    if not df_trades.empty:
        st.dataframe(df_trades.drop(columns=["Date_dt"], errors="ignore").iloc[::-1], use_container_width=True, hide_index=True)
        csv_bytes = df_trades.to_csv(index=False).encode('utf-8')
        st.download_button("📥 Download Excel / CSV Report", data=csv_bytes, file_name="my_30day_option_trades.csv", mime="text/csv")
    else:
        st.caption("No trades recorded yet.")
