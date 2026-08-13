
import streamlit as st
from SmartApi import SmartConnect
from datetime import datetime
from zoneinfo import ZoneInfo
import requests, math, json

st.set_page_config(page_title="NIFTY Advanced Risk Engine", layout="wide")
st.title("NIFTY CE/PE — Advanced Risk Engine")
st.caption("Angel One SmartAPI • Read-only • No orders are placed")

MASTER_URL="https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"

@st.cache_data(ttl=3600)
def load_master():
    r=requests.get(MASTER_URL,timeout=30)
    r.raise_for_status()
    return r.json()

def num(v):
    try:return float(v)
    except:return 0.0

def expiry_date(v):
    s=str(v).upper()
    for f in ("%d%b%Y","%d-%b-%Y","%d%b%y"):
        try:return datetime.strptime(s,f).date()
        except:pass
    return None

# ---------- LOGIN ----------
st.sidebar.header("Angel One Login")
api=st.sidebar.text_input("API Key",type="password",autocomplete="off")
client=st.sidebar.text_input("Client ID",autocomplete="off")
pin=st.sidebar.text_input("PIN / MPIN",type="password",autocomplete="off")
totp=st.sidebar.text_input("Current 6-digit TOTP",max_chars=6,type="password",autocomplete="one-time-code")

if st.sidebar.button("Login to Angel One",type="primary"):
    try:
        if not all([api.strip(),client.strip(),pin.strip(),totp.strip()]):
            st.sidebar.error("Fill all four login fields.")
            st.stop()
        obj=SmartConnect(api_key=api.strip())
        data=obj.generateSession(client.strip(),pin.strip(),totp.strip())
        if not isinstance(data,dict) or data.get("status") is False:
            raise RuntimeError(data.get("message","Login failed") if isinstance(data,dict) else str(data))
        st.session_state.obj=obj
        st.session_state.client=client.strip()
        st.session_state.api=api.strip()
        st.sidebar.success("Connected")
    except Exception as e:
        st.sidebar.error(f"Login failed: {e}")

if "obj" not in st.session_state:
    st.info("Login first using the Angel One credentials in the left sidebar.")
    st.stop()

obj=st.session_state.obj

# ---------- MASTER ----------
try:
    master=load_master()
except Exception as e:
    st.error(f"Could not load Angel One instrument master: {e}")
    st.stop()

today=datetime.now(ZoneInfo("Asia/Kolkata")).date()
contracts=[]
for x in master:
    if x.get("exch_seg")!="NFO" or x.get("name")!="NIFTY" or x.get("instrumenttype")!="OPTIDX":
        continue
    d=expiry_date(x.get("expiry",""))
    if not d or d<today: continue
    try: strike=num(x.get("strike"))/100
    except: continue
    sym=x.get("symbol","")
    if not sym.endswith(("CE","PE")): continue
    contracts.append({
        "expiry":d,"strike":strike,"symbol":sym,"token":str(x.get("token")),
        "lotsize":int(num(x.get("lotsize")) or 65),"raw_expiry":x.get("expiry","")
    })

# ---------- NIFTY ----------
try:
    n = obj.ltpData("NSE", "Nifty 50", "99926000")

    if not isinstance(n, dict):
        raise RuntimeError(f"Unexpected Angel One response: {n}")

    if n.get("status") is False:
        msg = n.get("message") or n.get("errorcode") or "Unknown Angel One error"
        raise RuntimeError(f"Angel One API error: {msg}")

    data = n.get("data")

    if isinstance(data, dict) and isinstance(data.get("fetched"), list):
        fetched = data.get("fetched")

        if not fetched:
            raise RuntimeError(
                "NIFTY data is currently unavailable from Angel One."
            )

        ltp_value = fetched[0].get("ltp")

    elif isinstance(data, dict):
        ltp_value = data.get("ltp")

    elif isinstance(data, list) and data:
        ltp_value = data[0].get("ltp")

    else:
        raise RuntimeError(
            f"Unexpected NIFTY data format: {data}"
        )

    if ltp_value is None:
        raise RuntimeError(
            f"NIFTY LTP not found in Angel One response: {n}"
        )

    nifty = num(ltp_value)

    if nifty <= 0:
        raise RuntimeError(f"Invalid NIFTY LTP: {ltp_value}")

except Exception as e:
    st.error(f"NIFTY live price error: {e}")
    st.stop()

# ---------- TRADE INPUT ----------
st.sidebar.header("Trade Input")
exps=sorted({x["expiry"] for x in contracts})
expiry=st.sidebar.selectbox("Expiry",exps,format_func=lambda d:d.strftime("%d-%b-%Y"))
opt=st.sidebar.selectbox("Option",["CE","PE"])
rows=[x for x in contracts if x["expiry"]==expiry and x["symbol"].endswith(opt)]
strikes=sorted({x["strike"] for x in rows})
near=min(strikes,key=lambda s:abs(s-nifty))
strike=st.sidebar.selectbox("Strike",strikes,index=strikes.index(near),format_func=lambda s:f"{s:,.0f}")
contract=next(x for x in rows if x["strike"]==strike)

lots=st.sidebar.number_input("Lots",1,1000,1,1)
qty=lots*contract["lotsize"]

# These placeholders are filled automatically after Angel One returns the live quote/model.
current_premium_box=st.sidebar.empty()
sl_premium_box=st.sidebar.empty()
target_premium_box=st.sidebar.empty()

sl=st.sidebar.number_input("NIFTY SL",value=float(round(nifty/50)*50-50),step=50.0)
target=st.sidebar.number_input("NIFTY Target",value=float(round(nifty/50)*50+150),step=50.0)

st.sidebar.header("Optional Risk Control")
use_risk=st.sidebar.checkbox("Use Maximum Loss Allowed",False)
max_loss=st.sidebar.number_input("Maximum Loss Allowed ₹",0.0,100000000.0,3000.0,100.0,disabled=not use_risk)

# ---------- QUOTE ----------
try:
    q=obj.getMarketData("FULL",{"NFO":[contract["token"]]})
    fetched=(q.get("data",{}).get("fetched") or [])
    if not fetched: raise RuntimeError(str(q))
    q=fetched[0]
except Exception as e:
    st.error(f"Option quote error: {e}")
    st.stop()

ltp=num(q.get("ltp"))
bid=num(q.get("bestBidPrice") or q.get("bestPrice"))
ask=num(q.get("bestAskPrice"))
volume=num(q.get("tradeVolume") or q.get("volume"))
oi=num(q.get("opnInterest") or q.get("openInterest"))
avg=num(q.get("avgPrice") or q.get("averagePrice"))
high=num(q.get("high")); low=num(q.get("low"))

if ltp <= 0:
    st.error("Selected option has no valid live premium from Angel One.")
    st.stop()

# Current premium is fetched automatically for the selected expiry/option/strike.
entry=ltp
current_premium_box.metric("Current Premium", f"₹{ltp:,.2f}")

# ---------- GREEKS ----------
greek=None
greek_error=None
try:
    gd=obj.optionGreek({"name":"NIFTY","expirydate":contract["raw_expiry"]})
    data=gd.get("data") if isinstance(gd,dict) else None
    if not data: raise RuntimeError(gd.get("message","No Greeks data") if isinstance(gd,dict) else str(gd))
    greek=next((g for g in data if g.get("optionType")==opt and abs(num(g.get("strikePrice"))-strike)<1),None)
    if not greek: raise RuntimeError("Selected strike Greeks not found.")
except Exception as e:
    greek_error=str(e)

# ---------- LIVE SNAPSHOT ----------
st.subheader("📡 Live Market Snapshot")
c=st.columns(7)
for col,label,val in zip(c,["NIFTY","Option LTP","Bid","Ask","OI","Volume","Avg Price"],
                         [f"{nifty:,.2f}",f"₹{ltp:,.2f}",f"₹{bid:,.2f}",f"₹{ask:,.2f}",f"{oi:,.0f}",f"{volume:,.0f}",f"₹{avg:,.2f}"]):
    col.metric(label,val)

if greek:
    delta=num(greek.get("delta")); gamma=num(greek.get("gamma"))
    theta=num(greek.get("theta")); vega=num(greek.get("vega"))
    iv=num(greek.get("impliedVolatility"))
    c=st.columns(5)
    for col,label,val in zip(c,["Delta","Gamma","Theta / day","Vega / 1 IV pt","IV"],
                             [f"{delta:.4f}",f"{gamma:.6f}",f"₹{theta:.2f}",f"₹{vega:.2f}",f"{iv:.2f}%"]):
        col.metric(label,val)
else:
    st.warning(f"Greeks unavailable right now: {greek_error}")
    st.stop()

# ---------- SELECTED-STRIKE SL / TARGET MODEL ----------
# IMPORTANT:
# All SL/Target premium estimates start from the LIVE premium of the
# EXACT option contract selected above (same expiry + same CE/PE + same strike).
# They do NOT start from ATM premium or another strike's premium.

# A simple time proxy is used only for theta. It is not a market forecast.
day_range = max(high - low, nifty * 0.002)
speed = max(day_range / 390.0, nifty * 0.000004)

sl_minutes = max(1.0, abs(sl - nifty) / speed)
target_minutes = max(1.0, abs(target - nifty) / speed)

def expected_selected_premium(nifty_level, minutes):
    """
    Reprice the SAME selected option contract from its current live LTP.
    Delta and Gamma are for the selected strike/expiry/CE-PE contract.
    Theta is applied for the estimated time movement.
    Vega is not stressed here, so IV is assumed unchanged.
    """
    d_nifty = nifty_level - nifty

    estimated = (
        ltp
        + (delta * d_nifty)
        + (0.5 * gamma * d_nifty * d_nifty)
        + (theta * (minutes / (24 * 60)))
    )

    return max(0.0, estimated)

# Expected premium of the EXACT selected strike at SL and Target.
sl_expected_premium = expected_selected_premium(sl, sl_minutes)
target_expected_premium = expected_selected_premium(target, target_minutes)

# Per-share P&L for the selected contract.
sl_pnl_per_share = sl_expected_premium - entry
target_pnl_per_share = target_expected_premium - entry

# Total P&L for the selected number of lots.
sl_pnl_total = sl_pnl_per_share * qty
target_pnl_total = target_pnl_per_share * qty

# For a long option:
# negative P&L = loss, positive P&L = profit.
sl_loss_total = max(0.0, -sl_pnl_total)
target_profit_total = max(0.0, target_pnl_total)

# Show the exact calculation in the sidebar.
st.sidebar.divider()
st.sidebar.subheader("Automatic Premium & P&L")

st.sidebar.metric(
    f"Current {opt} {strike:,.0f} Premium",
    f"₹{entry:,.2f}"
)

st.sidebar.metric(
    f"Expected Premium @ NIFTY {sl:,.0f}",
    f"₹{sl_expected_premium:,.2f}"
)

if sl_pnl_total < 0:
    st.sidebar.error(
        f"Estimated SL Loss: ₹{sl_loss_total:,.0f}"
    )
else:
    st.sidebar.success(
        f"Estimated SL P&L: +₹{sl_pnl_total:,.0f}"
    )

st.sidebar.metric(
    f"Expected Premium @ NIFTY {target:,.0f}",
    f"₹{target_expected_premium:,.2f}"
)

if target_pnl_total > 0:
    st.sidebar.success(
        f"Estimated Target Profit: ₹{target_profit_total:,.0f}"
    )
else:
    st.sidebar.warning(
        f"Estimated Target P&L: ₹{target_pnl_total:,.0f}"
    )

st.sidebar.caption(
    "Estimate starts from the selected strike's LIVE premium and uses "
    "that exact contract's Delta + Gamma + Theta. IV change is not assumed."
)

# ---------- SL RESULT ----------
st.subheader("🛑 NIFTY SL → Selected Strike Premium & Loss")

st.write(
    f"**Selected:** {strike:,.0f} {opt}  |  "
    f"Entry premium: **₹{entry:,.2f}**  |  "
    f"Current NIFTY: **{nifty:,.2f}**"
)

sl_cols = st.columns(4)
sl_cols[0].metric("NIFTY SL", f"{sl:,.2f}")
sl_cols[1].metric("Expected Premium @ SL", f"₹{sl_expected_premium:,.2f}")
sl_cols[2].metric(
    "P&L / Share",
    f"₹{sl_pnl_per_share:,.2f}"
)
sl_cols[3].metric(
    "Total P&L",
    f"₹{sl_pnl_total:,.0f}"
)

if sl_pnl_total < 0:
    st.error(
        f"🔴 If NIFTY reaches {sl:,.2f}, the model estimates "
        f"{strike:,.0f} {opt} premium around ₹{sl_expected_premium:,.2f}. "
        f"Estimated loss ≈ ₹{sl_loss_total:,.0f} for {lots} lot(s)."
    )
else:
    st.success(
        f"🟢 At NIFTY {sl:,.2f}, the model estimates "
        f"{strike:,.0f} {opt} premium around ₹{sl_expected_premium:,.2f}."
    )

# ---------- TARGET RESULT ----------
st.subheader("🎯 NIFTY Target → Selected Strike Premium & Profit")

target_cols = st.columns(4)
target_cols[0].metric("NIFTY Target", f"{target:,.2f}")
target_cols[1].metric(
    "Expected Premium @ Target",
    f"₹{target_expected_premium:,.2f}"
)
target_cols[2].metric(
    "P&L / Share",
    f"₹{target_pnl_per_share:,.2f}"
)
target_cols[3].metric(
    "Total P&L",
    f"₹{target_pnl_total:,.0f}"
)

if target_pnl_total > 0:
    st.success(
        f"🟢 If NIFTY reaches {target:,.2f}, the model estimates "
        f"{strike:,.0f} {opt} premium around ₹{target_expected_premium:,.2f}. "
        f"Estimated profit ≈ ₹{target_profit_total:,.0f} for {lots} lot(s)."
    )
else:
    st.warning(
        f"At NIFTY {target:,.2f}, the model estimates "
        f"{strike:,.0f} {opt} premium around ₹{target_expected_premium:,.2f}."
    )

# ---------- SIMPLE TRADE SUMMARY ----------
st.subheader("⚖️ Selected Strike Trade Summary")

summary = st.columns(6)
summary[0].metric("Strike", f"{strike:,.0f} {opt}")
summary[1].metric("Entry Premium", f"₹{entry:,.2f}")
summary[2].metric("SL Premium", f"₹{sl_expected_premium:,.2f}")
summary[3].metric("SL Loss", f"₹{sl_loss_total:,.0f}")
summary[4].metric("Target Premium", f"₹{target_expected_premium:,.2f}")
summary[5].metric("Target Profit", f"₹{target_profit_total:,.0f}")

# Approximate reward:risk based on the same selected strike.
reward_risk = (
    target_profit_total / sl_loss_total
    if sl_loss_total > 0 else float("inf")
)

if math.isfinite(reward_risk):
    st.info(
        f"**Reward : Risk ≈ {reward_risk:.2f} : 1** "
        f"(based on the selected {strike:,.0f} {opt} contract)."
    )
else:
    st.info("Reward : Risk cannot be calculated because estimated SL loss is zero.")

# ---------- OPTIONAL RISK CONTROL ----------
if use_risk:
    st.subheader("🔴 Maximum Loss Control")
    one_lot_cons=cons_loss/max(1,lots)
    max_lots=int(max_loss//one_lot_cons) if one_lot_cons>0 else 0
    buffer=max_loss-cons_loss
    c=st.columns(4)
    c[0].metric("Allowed Risk",f"₹{max_loss:,.0f}")
    c[1].metric("Conservative Risk",f"₹{cons_loss:,.0f}")
    c[2].metric("Risk Buffer",f"₹{buffer:,.0f}")
    c[3].metric("Status","🟢 OK" if cons_loss<=max_loss else "🔴 TOO HIGH")
    st.write(f"**Conservative risk per 1 lot:** ₹{one_lot_cons:,.0f}")
    st.write(f"### Suggested maximum: **{max_lots} lot(s)**")
    if max_lots==0:
        st.error("Even 1 lot exceeds your optional risk limit.")
    elif lots>max_lots:
        st.warning(f"Current {lots} lot(s) exceed the optional limit. Consider no more than {max_lots} lot(s).")
    else:
        st.success(f"Current {lots} lot(s) are within the optional limit.")

# ---------- RELIABILITY ----------
spread=ask-bid if bid>0 and ask>0 else 0
spread_pct=spread/ltp*100 if ltp>0 else 0
warnings=[]
if spread_pct>1: warnings.append(f"Bid/ask spread is {spread_pct:.2f}% of LTP")
if volume<1000: warnings.append("Low option volume")
if oi<=0: warnings.append("OI unavailable")
if not greek: warnings.append("Greeks unavailable")
st.subheader("🧭 Model Reliability")
if warnings:
    st.warning(" | ".join(warnings))
else:
    st.success("No major data-quality warning detected.")

st.caption("Important: This is a model estimate, not a guaranteed fill or guaranteed maximum loss. Gaps, IV jumps/crushes, spread widening, liquidity and slippage can materially change actual P&L. Greeks are live-contract data; Angel One notes that Greeks are available for live contracts and its forum contains reports of occasional expiry/Greek discrepancies, so treat them as inputs rather than certainty.")

# ---------- REFRESH ----------
if st.button("🔄 Refresh Live Data"):
    st.rerun()
