
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
        raise RuntimeError(f"Unexpected NIFTY response: {n}")

    data = n.get("data")

    if isinstance(data, dict):
        ltp_value = data.get("ltp")
    elif isinstance(data, list) and data:
        ltp_value = data[0].get("ltp")
    else:
        raise RuntimeError(f"Unexpected NIFTY data format: {data}")

    if ltp_value is None:
        raise RuntimeError(f"NIFTY LTP not found in response: {n}")

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
entry=st.sidebar.number_input("Entry Premium ₹",0.05,100000.0,100.0,0.05)
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

# ---------- MODEL ----------
# Uses local delta/gamma repricing + theta + vega stress.
# Time estimate uses current day range as a coarse speed proxy; it is explicitly not a forecast.
day_range=max(high-low,nifty*0.002)
speed=max(day_range/390.0,nifty*0.000004)
sl_minutes=max(1.0,abs(sl-nifty)/speed)
target_minutes=max(1.0,abs(target-nifty)/speed)

def theoretical_premium(level,minutes,iv_shift):
    d=level-nifty
    p=ltp + delta*d + 0.5*gamma*d*d + theta*(minutes/(24*60)) + vega*iv_shift
    return max(0.0,p)

def exit_reference(p):
    # For a long option, the executable-side reference is bid.
    # If bid is unavailable, fall back to model price.
    return min(p,bid) if bid>0 else p

# Loss scenarios: "Favorable" is lower stress; Conservative is more adverse.
loss_defs=[
    ("Favorable",+1.0,0.95,0.0),
    ("Base",0.0,1.00,0.0),
    ("High Risk",-1.5,1.10,0.5),
    ("Conservative",-3.0,1.25,1.0),
]
loss_rows=[]
for name,iv_shift,stress,slip in loss_defs:
    p=theoretical_premium(sl,sl_minutes,iv_shift)
    px=max(0.0,exit_reference(p)*(1-slip/100))
    loss=max(0.0,(entry-px))*qty*stress
    loss_rows.append((name,p,px,loss))

profit_defs=[
    ("Conservative",-2.0,0.90),
    ("Base",0.0,1.00),
    ("Favorable",+2.0,1.10),
]
profit_rows=[]
for name,iv_shift,stress in profit_defs:
    p=theoretical_premium(target,target_minutes,iv_shift)
    px=max(0.0,exit_reference(p))
    profit=max(0.0,(px-entry))*qty*stress
    profit_rows.append((name,p,px,profit))

st.subheader("🛑 NIFTY SL → Estimated Loss")
st.write(f"NIFTY **{nifty:,.2f} → {sl:,.2f}** | model time proxy: **{sl_minutes:.0f} min**")
cols=st.columns(4)
for col,(name,p,px,loss) in zip(cols,loss_rows):
    col.metric(name,f"₹{loss:,.0f}",f"exit premium ≈ ₹{px:,.2f}")

st.subheader("🎯 NIFTY Target → Expected Profit")
st.write(f"NIFTY **{nifty:,.2f} → {target:,.2f}** | model time proxy: **{target_minutes:.0f} min**")
cols=st.columns(3)
for col,(name,p,px,profit) in zip(cols,profit_rows):
    col.metric(name,f"₹{profit:,.0f}",f"exit premium ≈ ₹{px:,.2f}")

base_loss=loss_rows[1][3]
cons_loss=loss_rows[3][3]
base_profit=profit_rows[1][3]
rr=(base_profit/base_loss) if base_loss else float("inf")

st.subheader("⚖️ Trade Summary")
c=st.columns(5)
for col,label,val in zip(c,["Base Loss","Conservative Loss","Base Profit","Reward : Risk","Theoretical Max Loss"],
                         [f"₹{base_loss:,.0f}",f"₹{cons_loss:,.0f}",f"₹{base_profit:,.0f}",
                          f"{rr:.2f}:1" if math.isfinite(rr) else "∞",f"₹{entry*qty:,.0f}"]):
    col.metric(label,val)

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
