import os
import csv
import json
import time
import pyotp
import requests
import pandas as pd
from datetime import datetime, timedelta, timezone
from SmartApi import SmartConnect

# ==========================================
# 🔐 1. APNI ANGEL ONE DETAILS YAHAN BHARO
# ==========================================
API_KEY = "YOUR_API_KEY_HERE"
CLIENT_ID = "YOUR_CLIENT_ID_HERE"
MPIN = "YOUR_4_DIGIT_MPIN"
TOTP_SECRET = "YOUR_TOTP_STRING_HERE"

# ==========================================
# 📲 2. APNI TELEGRAM DETAILS YAHAN BHARO
# ==========================================
TELEGRAM_BOT_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN_HERE"
TELEGRAM_CHAT_ID = "YOUR_TELEGRAM_CHAT_ID_HERE"

# ==========================================
# ⚙️ 3. STRATEGY & WALLET SETTINGS
# ==========================================
STARTING_CAPITAL = 25000.0
LOT_SIZE = 65
BROKERAGE = 60.0

# False = 3:20 PM Intraday Auto-Exit (Recommended for Cheap OTM)
# True  = Hold trade overnight & resume next morning at 9:15 AM
ALLOW_CARRY_FORWARD = False

CSV_FILE = "trades_history.csv"
OPEN_POS_FILE = "open_position.json"
IST = timezone(timedelta(hours=5, minutes=30))

def get_ist_now():
    return datetime.now(IST)

def init_database():
    if not os.path.exists(CSV_FILE):
        with open(CSV_FILE, mode="w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "Date", "Entry_Time", "Exit_Time", "Signal", "Contract",
                "Entry_Price", "Exit_Price", "Gross_PnL", "Brokerage", "Net_PnL", "Exit_Reason"
            ])
        return STARTING_CAPITAL
    else:
        df = pd.read_csv(CSV_FILE)
        total_pnl = df["Net_PnL"].sum() if not df.empty else 0.0
        return STARTING_CAPITAL + total_pnl

def save_trade_to_db(trade_data):
    with open(CSV_FILE, mode="a", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            trade_data["date"], trade_data["entry_time"], trade_data["exit_time"],
            trade_data["signal"], trade_data["contract"], trade_data["entry"],
            trade_data["exit"], round(trade_data["gross_pnl"], 2),
            BROKERAGE, round(trade_data["net_pnl"], 2), trade_data["reason"]
        ])

def load_carried_trade():
    if os.path.exists(OPEN_POS_FILE):
        try:
            with open(OPEN_POS_FILE, "r") as f:
                data = json.load(f)
                return data if data else None
        except Exception:
            return None
    return None

def save_carried_trade(active_trade):
    with open(OPEN_POS_FILE, "w") as f:
        json.dump(active_trade if active_trade else {}, f)

def send_telegram(message):
    print(message)
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}, timeout=5)
    except Exception as e:
        print("Telegram Error:", e)

def connect_angel():
    smartApi = SmartConnect(api_key=API_KEY)
    totp = pyotp.TOTP(TOTP_SECRET).now()
    data = smartApi.generateSession(CLIENT_ID, MPIN, totp)
    if not data['status']:
        send_telegram(f"❌ Angel Login Failed: {data['message']}")
        return None
    return smartApi

def get_nifty_tokens():
    url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
    df = pd.DataFrame(requests.get(url).json())
    nifty_opts = df[(df['name'] == 'NIFTY') & (df['instrumenttype'] == 'OPTIDX')].copy()
    nifty_opts['expiry'] = pd.to_datetime(nifty_opts['expiry'], format='%d%b%Y')
    nearest_exp = nifty_opts[nifty_opts['expiry'] >= pd.Timestamp.today().normalize()]['expiry'].min()
    weekly_df = nifty_opts[nifty_opts['expiry'] == nearest_exp].copy()
    weekly_df['strike_price'] = weekly_df['strike'].astype(float) / 100.0
    return weekly_df, nearest_exp.strftime('%d-%b-%Y')

def run_auto_bot():
    wallet = init_database()
    now = get_ist_now()

    while now.hour == 9 and now.minute < 15:
        time.sleep(15)
        now = get_ist_now()

    obj = connect_angel()
    if not obj:
        return
    weekly_df, exp_str = get_nifty_tokens()

    active_trade = load_carried_trade()
    trades_today = 0
    today_pnl = 0.0
    spot_history = []
    prev_ce_oi, prev_pe_oi = None, None

    carry_msg = f"\n🔄 <b>Resumed Carried Trade:</b> {active_trade['symbol']} (Entry: ₹{active_trade['entry']})" if active_trade else ""
    send_telegram(
        f"🌅 <b>9:15 AM AUTO-BOT STARTED!</b>\n"
        f"📅 Expiry: {exp_str}\n"
        f"💰 Wallet Balance: <b>₹{wallet:,.2f}</b>{carry_msg}"
    )

    while True:
        now = get_ist_now()

        # Market Close at 3:30 PM IST
        if (now.hour == 15 and now.minute >= 30) or now.hour > 15:
            if active_trade and ALLOW_CARRY_FORWARD:
                save_carried_trade(active_trade)
                send_telegram(f"🌙 <b>POSITION CARRIED OVERNIGHT:</b> {active_trade['symbol']}")
            send_telegram(
                f"🏁 <b>3:30 PM MARKET CLOSED</b>\n"
                f"Today's Closed Trades: {trades_today} | Today's Net P&L: <b>₹{today_pnl:+.2f}</b>\n"
                f"💰 Updated Wallet: <b>₹{wallet:,.2f}</b>"
            )
            break

        try:
            spot_res = obj.ltpData("NSE", "Nifty 50", "99926000")
            spot_ltp = float(spot_res['data']['ltp'])
            spot_history.append(spot_ltp)

            atm_strike = round(spot_ltp / 50) * 50
            otm_ce_strike = atm_strike + 100
            otm_pe_strike = atm_strike - 100

            atm_ce_row = weekly_df[(weekly_df['strike_price'] == atm_strike) & (weekly_df['symbol'].str.endswith('CE'))].iloc[0]
            atm_pe_row = weekly_df[(weekly_df['strike_price'] == atm_strike) & (weekly_df['symbol'].str.endswith('PE'))].iloc[0]
            otm_ce_row = weekly_df[(weekly_df['strike_price'] == otm_ce_strike) & (weekly_df['symbol'].str.endswith('CE'))].iloc[0]
            otm_pe_row = weekly_df[(weekly_df['strike_price'] == otm_pe_strike) & (weekly_df['symbol'].str.endswith('PE'))].iloc[0]

            tokens = [atm_ce_row['token'], atm_pe_row['token'], otm_ce_row['token'], otm_pe_row['token']]
            mkt_data = obj.getMarketData("FULL", {"NFO": tokens})['data']['fetched']
            token_map = {item['symbolToken']: item for item in mkt_data}

            ce_oi = float(token_map[atm_ce_row['token']]['opnInterest'])
            pe_oi = float(token_map[atm_pe_row['token']]['opnInterest'])
            otm_ce_ltp = float(token_map[otm_ce_row['token']]['ltp'])
            otm_pe_ltp = float(token_map[otm_pe_row['token']]['ltp'])

            # 1. MANAGE ACTIVE TRADE
            if active_trade:
                curr_res = obj.ltpData("NFO", active_trade['symbol'], active_trade['token'])
                curr_ltp = float(curr_res['data']['ltp'])

                if curr_ltp >= active_trade['entry'] * 1.8 and active_trade['sl'] < active_trade['entry']:
                    active_trade['sl'] = active_trade['entry']
                    save_carried_trade(active_trade)
                    send_telegram(f"🔒 <b>Trailing SL moved to Cost</b> for {active_trade['symbol']} @ ₹{active_trade['entry']}")

                is_eod_squareoff = (not ALLOW_CARRY_FORWARD) and (now.hour == 15 and now.minute >= 20)

                if curr_ltp >= active_trade['target'] or curr_ltp <= active_trade['sl'] or is_eod_squareoff:
                    if is_eod_squareoff:
                        reason = "3:20 PM INTRADAY AUTO-EXIT ⏰"
                    elif curr_ltp >= active_trade['target']:
                        reason = "TARGET HIT 🎯"
                    else:
                        reason = "STOP-LOSS HIT ❌"

                    gross_pnl = (curr_ltp - active_trade['entry']) * LOT_SIZE
                    net_pnl = gross_pnl - BROKERAGE
                    wallet += net_pnl
                    today_pnl += net_pnl
                    trades_today += 1

                    save_trade_to_db({
                        "date": now.strftime("%Y-%m-%d"),
                        "entry_time": active_trade["entry_time"],
                        "exit_time": now.strftime("%H:%M:%S"),
                        "signal": active_trade["signal"],
                        "contract": active_trade["symbol"],
                        "entry": active_trade["entry"],
                        "exit": curr_ltp,
                        "gross_pnl": gross_pnl,
                        "net_pnl": net_pnl,
                        "reason": reason
                    })

                    send_telegram(
                        f"🔔 <b>TRADE CLOSED & SAVED ({reason})</b>\n"
                        f"Contract: {active_trade['symbol']}\n"
                        f"Entry: ₹{active_trade['entry']} ➔ Exit: ₹{curr_ltp}\n"
                        f"Net P&L: <b>₹{net_pnl:+.2f}</b> | 💰 Wallet: <b>₹{wallet:,.2f}</b>"
                    )
                    active_trade = None
                    save_carried_trade(None)

            # 2. SCAN FOR NEW OTM BIG-MOVE SIGNAL (Before 3:00 PM)
            elif prev_ce_oi is not None and len(spot_history) > 3 and now.hour < 15:
                rec_high = max(spot_history[:-1])
                rec_low = min(spot_history[:-1])

                if spot_ltp > rec_high and ce_oi < prev_ce_oi and pe_oi > prev_pe_oi:
                    active_trade = {
                        "signal": "BUY_CE", "symbol": otm_ce_row['symbol'], "token": otm_ce_row['token'],
                        "entry_time": now.strftime("%H:%M:%S"), "entry": otm_ce_ltp,
                        "sl": round(otm_ce_ltp * 0.65, 1), "target": round(otm_ce_ltp * 2.5, 1)
                    }
                    save_carried_trade(active_trade)
                    send_telegram(
                        f"🚀 <b>OTM GAMMA BLAST (BUY CE)</b>\n"
                        f"📌 Contract: <b>{active_trade['symbol']}</b>\n"
                        f"💵 Entry: ₹{otm_ce_ltp} | 🛑 SL: ₹{active_trade['sl']} | 🎯 Target: ₹{active_trade['target']}"
                    )

                elif spot_ltp < rec_low and pe_oi < prev_pe_oi and ce_oi > prev_ce_oi:
                    active_trade = {
                        "signal": "BUY_PE", "symbol": otm_pe_row['symbol'], "token": otm_pe_row['token'],
                        "entry_time": now.strftime("%H:%M:%S"), "entry": otm_pe_ltp,
                        "sl": round(otm_pe_ltp * 0.65, 1), "target": round(otm_pe_ltp * 2.5, 1)
                    }
                    save_carried_trade(active_trade)
                    send_telegram(
                        f"🚀 <b>OTM GAMMA BLAST (BUY PE)</b>\n"
                        f"📌 Contract: <b>{active_trade['symbol']}</b>\n"
                        f"💵 Entry: ₹{otm_pe_ltp} | 🛑 SL: ₹{active_trade['sl']} | 🎯 Target: ₹{active_trade['target']}"
                    )

            prev_ce_oi, prev_pe_oi = ce_oi, pe_oi
            time.sleep(30)

        except Exception as e:
            print("Tick error:", e)
            time.sleep(10)

if __name__ == "__main__":
    run_auto_bot()
