import streamlit as st
from SmartApi import SmartConnect
from datetime import datetime, time
from zoneinfo import ZoneInfo
import requests
import math

# ============================================================
# NIFTY CE/PE — Advanced Risk Engine
# Read-only: no orders are placed.
#
# Premium projection method:
# 1) Fetch the LIVE premium of the exact selected option contract.
# 2) Fetch Angel One's live Greeks + IV for that exact strike/expiry.
# 3) Calibrate an "effective IV" to the LIVE premium so the model starts
#    exactly from the observed market price.
# 4) Re-price the SAME selected contract at the user's NIFTY SL/Target
#    using the calibrated IV and the SAME expiry.
#
# This is a theoretical constant-IV estimate, not a guaranteed future LTP.
# ============================================================

st.set_page_config(
    page_title="NIFTY Advanced Risk Engine",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

MASTER_URL = (
    "https://margincalculator.angelbroking.com/"
    "OpenAPI_File/files/OpenAPIScripMaster.json"
)
IST = ZoneInfo("Asia/Kolkata")

# ---------- SMALL HELPERS ----------

def num(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return default


def expiry_date(v):
    s = str(v).upper().strip()
    for f in ("%d%b%Y", "%d-%b-%Y", "%d%b%y"):
        try:
            return datetime.strptime(s, f).date()
        except Exception:
            pass
    return None


def fmt_inr(v):
    return f"₹{v:,.2f}"


def norm_expiry_for_greek(raw):
    """Angel One optionGreek expects e.g. 18AUG2026."""
    d = expiry_date(raw)
    return d.strftime("%d%b%Y").upper() if d else str(raw).upper()


def norm_greek_strike(v):
    """
    Angel's Greeks response generally returns strikePrice in normal
    index-point units (e.g. 24300), while some instrument-master fields
    are scaled by 100.
    """
    return num(v)


# ---------- BLACK-SCHOLES ----------

def norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_price(spot, strike, t_years, rate, div_yield, sigma, option_type):
    if spot <= 0 or strike <= 0:
        return 0.0
    if t_years <= 0 or sigma <= 0:
        intrinsic = max(spot - strike, 0.0) if option_type == "CE" else max(strike - spot, 0.0)
        return intrinsic

    sqrt_t = math.sqrt(t_years)
    d1 = (
        math.log(spot / strike)
        + (rate - div_yield + 0.5 * sigma * sigma) * t_years
    ) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t

    if option_type == "CE":
        return (
            spot * math.exp(-div_yield * t_years) * norm_cdf(d1)
            - strike * math.exp(-rate * t_years) * norm_cdf(d2)
        )
    return (
        strike * math.exp(-rate * t_years) * norm_cdf(-d2)
        - spot * math.exp(-div_yield * t_years) * norm_cdf(-d1)
    )


def implied_vol_from_price(
    market_price, spot, strike, t_years, rate, div_yield, option_type
):
    """
    Robust bisection IV solver.
    The returned IV is calibrated to the selected contract's LIVE LTP,
    so the model's current theoretical price is anchored to the market.
    """
    if market_price <= 0 or spot <= 0 or strike <= 0:
        return None

    intrinsic = (
        max(spot - strike, 0.0)
        if option_type == "CE"
        else max(strike - spot, 0.0)
    )

    # If LTP is below intrinsic, IV cannot be solved meaningfully.
    if market_price < intrinsic - 1e-6:
        return None

    lo, hi = 0.0001, 5.0
    p_lo = bs_price(spot, strike, t_years, rate, div_yield, lo, option_type)
    p_hi = bs_price(spot, strike, t_years, rate, div_yield, hi, option_type)

    if market_price < p_lo - 1e-6 or market_price > p_hi + 1e-6:
        return None

    for _ in range(80):
        mid = (lo + hi) / 2.0
        p_mid = bs_price(spot, strike, t_years, rate, div_yield, mid, option_type)
        if p_mid > market_price:
            hi = mid
        else:
            lo = mid

    return (lo + hi) / 2.0


def expiry_fraction_years(expiry):
    """
    Uses 15:30 IST as the option-market expiry cutoff.
    This is used only for theoretical pricing time-to-expiry.
    """
    now = datetime.now(IST)
    expiry_dt = datetime.combine(expiry, time(15, 30), tzinfo=IST)
    seconds = max((expiry_dt - now).total_seconds(), 60.0)
    return seconds / (365.0 * 24.0 * 3600.0)


# ---------- MASTER ----------

@st.cache_data(ttl=3600)
def load_master():
    r = requests.get(MASTER_URL, timeout=30)
    r.raise_for_status()
    return r.json()


# ---------- LOGIN ----------

st.sidebar.markdown("## 🔐 Angel One Login")
api = st.sidebar.text_input(
    "API Key", type="password", autocomplete="off"
)
client = st.sidebar.text_input(
    "Client ID", autocomplete="off"
)
pin = st.sidebar.text_input(
    "PIN / MPIN", type="password", autocomplete="off"
)
totp = st.sidebar.text_input(
    "Current 6-digit TOTP",
    max_chars=6,
    type="password",
    autocomplete="one-time-code",
)

if st.sidebar.button("🔗 Login to Angel One", type="primary", use_container_width=True):
    try:
        if not all([api.strip(), client.strip(), pin.strip(), totp.strip()]):
            st.sidebar.error("Please fill all four login fields.")
            st.stop()

        obj = SmartConnect(api_key=api.strip())
        data = obj.generateSession(
            client.strip(), pin.strip(), totp.strip()
        )

        if not isinstance(data, dict) or data.get("status") is False:
            msg = (
                data.get("message", "Login failed")
                if isinstance(data, dict)
                else str(data)
            )
            raise RuntimeError(msg)

        st.session_state.obj = obj
        st.session_state.client = client.strip()
        st.sidebar.success("✅ Connected")
    except Exception as e:
        st.sidebar.error(f"Login failed: {e}")

if "obj" not in st.session_state:
    st.title("📈 NIFTY CE/PE — Advanced Risk Engine")
    st.caption("Angel One SmartAPI • Read-only • No orders are placed")
    st.info("Login first using the Angel One credentials in the left sidebar.")
    st.stop()

obj = st.session_state.obj

# ---------- MASTER / CONTRACTS ----------

try:
    master = load_master()
except Exception as e:
    st.error(f"Could not load Angel One instrument master: {e}")
    st.stop()

today = datetime.now(IST).date()
contracts = []

for x in master:
    if (
        x.get("exch_seg") != "NFO"
        or x.get("name") != "NIFTY"
        or x.get("instrumenttype") != "OPTIDX"
    ):
        continue

    d = expiry_date(x.get("expiry", ""))
    if not d or d < today:
        continue

    strike = num(x.get("strike")) / 100.0
    if strike <= 0:
        continue

    sym = x.get("symbol", "")
    if not sym.endswith(("CE", "PE")):
        continue

    contracts.append(
        {
            "expiry": d,
            "strike": strike,
            "symbol": sym,
            "token": str(x.get("token")),
            "lotsize": int(num(x.get("lotsize")) or 65),
            "raw_expiry": x.get("expiry", ""),
        }
    )

if not contracts:
    st.error("No active NIFTY option contracts found.")
    st.stop()

# ---------- NIFTY LIVE ----------

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
            raise RuntimeError("NIFTY data is currently unavailable.")
        ltp_value = fetched[0].get("ltp")
    elif isinstance(data, dict):
        ltp_value = data.get("ltp")
    elif isinstance(data, list) and data:
        ltp_value = data[0].get("ltp")
    else:
        raise RuntimeError(f"Unexpected NIFTY data format: {data}")

    nifty = num(ltp_value)
    if nifty <= 0:
        raise RuntimeError(f"Invalid NIFTY LTP: {ltp_value}")

except Exception as e:
    st.error(f"NIFTY live price error: {e}")
    st.stop()

# ---------- TRADE INPUT ----------

st.sidebar.divider()
st.sidebar.markdown("## 🎯 Trade Setup")

exps = sorted({x["expiry"] for x in contracts})
expiry = st.sidebar.selectbox(
    "Expiry",
    exps,
    format_func=lambda d: d.strftime("%d-%b-%Y"),
)

opt = st.sidebar.selectbox("Option", ["CE", "PE"])

rows = [
    x for x in contracts
    if x["expiry"] == expiry and x["symbol"].endswith(opt)
]
strikes = sorted({x["strike"] for x in rows})

near = min(strikes, key=lambda s: abs(s - nifty))
strike = st.sidebar.selectbox(
    "Strike",
    strikes,
    index=strikes.index(near),
    format_func=lambda s: f"{s:,.0f}",
)

contract = next(x for x in rows if x["strike"] == strike)

lots = st.sidebar.number_input(
    "Lots", min_value=1, max_value=1000, value=1, step=1
)
qty = int(lots) * contract["lotsize"]

# Direction-friendly defaults
if opt == "CE":
    default_sl = round((nifty - 100) / 50) * 50
    default_target = round((nifty + 150) / 50) * 50
else:
    default_sl = round((nifty + 100) / 50) * 50
    default_target = round((nifty - 150) / 50) * 50

sl = st.sidebar.number_input(
    "NIFTY SL",
    value=float(default_sl),
    step=50.0,
)
target = st.sidebar.number_input(
    "NIFTY Target",
    value=float(default_target),
    step=50.0,
)

# Advanced assumptions
with st.sidebar.expander("⚙️ Model Assumptions", expanded=False):
    rate_pct = st.number_input(
        "Risk-free rate %", min_value=0.0, max_value=15.0,
        value=6.0, step=0.25
    )
    div_pct = st.number_input(
        "Dividend yield %", min_value=0.0, max_value=10.0,
        value=0.0, step=0.25
    )
    iv_shift = st.slider(
        "IV scenario ± points",
        min_value=0.0, max_value=10.0,
        value=2.0, step=0.5,
        help="Used only to show an uncertainty range around the base estimate."
    )

# ---------- OPTION QUOTE ----------

try:
    quote = obj.getMarketData("FULL", {"NFO": [contract["token"]]})
    fetched = (quote.get("data", {}).get("fetched") or [])
    if not fetched:
        raise RuntimeError(str(quote))
    q = fetched[0]
except Exception as e:
    st.error(f"Option quote error: {e}")
    st.stop()

ltp = num(q.get("ltp"))
bid = num(q.get("bestBidPrice") or q.get("bestPrice"))
ask = num(q.get("bestAskPrice"))
volume = num(q.get("tradeVolume") or q.get("volume"))
oi = num(q.get("opnInterest") or q.get("openInterest"))
avg = num(q.get("avgPrice") or q.get("averagePrice"))
high = num(q.get("high"))
low = num(q.get("low"))

if ltp <= 0:
    st.error("Selected option has no valid live premium from Angel One.")
    st.stop()

entry = ltp

# ---------- GREEKS ----------

greek = None
greek_error = None

try:
    greek_request_expiry = norm_expiry_for_greek(contract["raw_expiry"])
    gd = obj.optionGreek(
        {
            "name": "NIFTY",
            "expirydate": greek_request_expiry,
        }
    )

    gdata = gd.get("data") if isinstance(gd, dict) else None
    if not gdata:
        raise RuntimeError(
            gd.get("message", "No Greeks data")
            if isinstance(gd, dict)
            else str(gd)
        )

    greek = next(
        (
            g for g in gdata
            if str(g.get("optionType", "")).upper() == opt
            and abs(norm_greek_strike(g.get("strikePrice")) - strike) < 1.0
        ),
        None,
    )

    if not greek:
        raise RuntimeError("Selected strike Greeks not found.")

except Exception as e:
    greek_error = str(e)

if not greek:
    st.warning(
        f"Live Greeks/IV are unavailable right now: {greek_error}. "
        "Premium projection cannot be made reliably without IV."
    )
    st.stop()

delta = num(greek.get("delta"))
gamma = num(greek.get("gamma"))
theta = num(greek.get("theta"))
vega = num(greek.get("vega"))
angel_iv_pct = num(greek.get("impliedVolatility"))

# ---------- MODEL ----------

rate = rate_pct / 100.0
div_yield = div_pct / 100.0
t_now = expiry_fraction_years(expiry)

# Calibrate IV to the ACTUAL selected contract LTP.
calibrated_iv = implied_vol_from_price(
    entry,
    nifty,
    strike,
    t_now,
    rate,
    div_yield,
    opt,
)

if calibrated_iv is None:
    # Fallback to Angel One IV only if calibration is mathematically impossible.
    if angel_iv_pct > 0:
        calibrated_iv = angel_iv_pct / 100.0
        calibration_note = (
            "Base IV fallback: Angel One live IV "
            "(current LTP could not be inverted cleanly)."
        )
    else:
        st.error(
            "Could not obtain a usable IV for this contract. "
            "No premium projection was generated."
        )
        st.stop()
else:
    calibration_note = (
        "Base IV is calibrated to the selected contract's live LTP, "
        "so the model starts from the actual observed premium."
    )

def projected_premium(nifty_level, iv):
    return max(
        0.0,
        bs_price(
            nifty_level,
            strike,
            t_now,
            rate,
            div_yield,
            iv,
            opt,
        ),
    )

# Base constant-IV estimate
sl_base = projected_premium(sl, calibrated_iv)
target_base = projected_premium(target, calibrated_iv)

# Uncertainty band from IV shock.
sl_low = projected_premium(
    sl, max(0.0001, calibrated_iv - iv_shift / 100.0)
)
sl_high = projected_premium(
    sl, calibrated_iv + iv_shift / 100.0
)

target_low = projected_premium(
    target, max(0.0001, calibrated_iv - iv_shift / 100.0)
)
target_high = projected_premium(
    target, calibrated_iv + iv_shift / 100.0
)

# P&L for a LONG option position.
sl_pnl_per_share = sl_base - entry
target_pnl_per_share = target_base - entry

sl_pnl_total = sl_pnl_per_share * qty
target_pnl_total = target_pnl_per_share * qty

sl_loss_total = max(0.0, -sl_pnl_total)
target_profit_total = max(0.0, target_pnl_total)

# P&L ranges from IV scenario.
sl_pnl_low = (sl_low - entry) * qty
sl_pnl_high = (sl_high - entry) * qty
target_pnl_low = (target_low - entry) * qty
target_pnl_high = (target_high - entry) * qty

spread = ask - bid if bid > 0 and ask > 0 else 0.0
spread_pct = spread / ltp * 100.0 if ltp > 0 else 0.0

# ---------- HEADER ----------

st.title("📈 NIFTY CE/PE — Advanced Risk Engine")
st.caption(
    "Angel One SmartAPI • Read-only • No orders are placed • "
    "Selected-strike premium projection"
)

# ---------- TOP TRADE CARD ----------

top = st.columns(5)
top[0].metric("NIFTY", f"{nifty:,.2f}")
top[1].metric("Selected", f"{strike:,.0f} {opt}")
top[2].metric("Live Premium", fmt_inr(entry))
top[3].metric("Lots", f"{lots} × {contract['lotsize']}")
top[4].metric("Position Qty", f"{qty:,}")

st.divider()

# ---------- LIVE SNAPSHOT ----------

st.subheader("📡 Live Market Snapshot")
snap = st.columns(7)
for col, label, value in zip(
    snap,
    ["NIFTY", "Option LTP", "Bid", "Ask", "OI", "Volume", "Avg Price"],
    [
        f"{nifty:,.2f}",
        fmt_inr(ltp),
        fmt_inr(bid),
        fmt_inr(ask),
        f"{oi:,.0f}",
        f"{volume:,.0f}",
        fmt_inr(avg),
    ],
):
    col.metric(label, value)

st.subheader("🧮 Selected Contract Greeks")
gc = st.columns(5)
for col, label, value in zip(
    gc,
    ["Delta", "Gamma", "Theta / day", "Vega / 1 IV pt", "Angel IV"],
    [
        f"{delta:.4f}",
        f"{gamma:.6f}",
        fmt_inr(theta),
        fmt_inr(vega),
        f"{angel_iv_pct:.2f}%",
    ],
):
    col.metric(label, value)

# ---------- MAIN SL / TARGET CARDS ----------

st.divider()
st.subheader("🎯 NIFTY Level → Selected Strike Premium")

sl_col, target_col = st.columns(2)

with sl_col:
    st.markdown("### 🛑 Stop Loss")
    st.metric("NIFTY SL", f"{sl:,.2f}")
    st.metric(
        f"Expected {strike:,.0f} {opt} Premium",
        fmt_inr(sl_base),
    )

    if sl_pnl_total < 0:
        st.error(
            f"Estimated loss: **{fmt_inr(sl_loss_total)}** "
            f"for {lots} lot(s)"
        )
    else:
        st.success(
            f"Estimated P&L: **+{fmt_inr(sl_pnl_total)}**"
        )

    st.write(
        f"**P&L / unit:** {fmt_inr(sl_pnl_per_share)}"
    )
    st.caption(
        f"IV scenario range: {fmt_inr(sl_low)} – {fmt_inr(sl_high)}"
    )

with target_col:
    st.markdown("### 🎯 Target")
    st.metric("NIFTY Target", f"{target:,.2f}")
    st.metric(
        f"Expected {strike:,.0f} {opt} Premium",
        fmt_inr(target_base),
    )

    if target_pnl_total > 0:
        st.success(
            f"Estimated profit: **+{fmt_inr(target_profit_total)}** "
            f"for {lots} lot(s)"
        )
    else:
        st.warning(
            f"Estimated P&L: **{fmt_inr(target_pnl_total)}**"
        )

    st.write(
        f"**P&L / unit:** {fmt_inr(target_pnl_per_share)}"
    )
    st.caption(
        f"IV scenario range: {fmt_inr(target_low)} – {fmt_inr(target_high)}"
    )

# ---------- SUMMARY ----------

st.divider()
st.subheader("⚖️ Trade Summary")

reward_risk = (
    target_profit_total / sl_loss_total
    if sl_loss_total > 0
    else None
)

summary = st.columns(6)
summary[0].metric("Entry Premium", fmt_inr(entry))
summary[1].metric("SL Premium", fmt_inr(sl_base))
summary[2].metric("SL Loss", fmt_inr(sl_loss_total))
summary[3].metric("Target Premium", fmt_inr(target_base))
summary[4].metric("Target Profit", fmt_inr(target_profit_total))
summary[5].metric(
    "Reward : Risk",
    f"{reward_risk:.2f} : 1" if reward_risk is not None else "N/A",
)

# ---------- MODEL QUALITY ----------

st.divider()
st.subheader("🧠 Estimate Quality")

q1, q2, q3 = st.columns(3)
q1.metric("Calibrated IV", f"{calibrated_iv * 100:.2f}%")
q2.metric("IV Scenario", f"±{iv_shift:.1f} pts")
q3.metric("Bid/Ask Spread", f"{spread_pct:.2f}%")

warnings = []

if spread_pct > 1.0:
    warnings.append(
        f"Bid/ask spread is {spread_pct:.2f}% of premium."
    )
if volume < 1000:
    warnings.append("Option volume is relatively low.")
if oi <= 0:
    warnings.append("Open interest is unavailable.")
if calibrated_iv * 100 > 60:
    warnings.append("Calibrated IV is unusually high.")
if t_now <= 1 / (365 * 24):
    warnings.append("Very little time remains to expiry; estimates can become unstable.")

if warnings:
    st.warning(" • ".join(warnings))
else:
    st.success("🟢 Live data quality looks reasonable.")

st.info(
    "How this estimate works: the app first anchors the model to the "
    "LIVE premium of the exact selected strike. It then calibrates IV to "
    "that premium and reprices the SAME contract at your NIFTY SL/Target "
    "with the same expiry. The main number assumes IV stays unchanged. "
    "The displayed range shows what happens if IV is ± the selected "
    "scenario amount. Actual market premium can still differ because IV, "
    "spread, liquidity and time-to-hit can change."
)

st.caption(
    calibration_note
    + " This is a theoretical estimate, not a guaranteed future LTP or fill."
)

# ---------- OPTIONAL RISK LIMIT ----------

with st.expander("🛡️ Optional Maximum Loss Control"):
    use_risk = st.checkbox("Use Maximum Loss Allowed", False)
    max_loss = st.number_input(
        "Maximum Loss Allowed ₹",
        min_value=0.0,
        max_value=100_000_000.0,
        value=3000.0,
        step=100.0,
        disabled=not use_risk,
    )

    if use_risk:
        conservative_loss = max(0.0, -sl_pnl_low)
        one_lot_loss = conservative_loss / max(1, int(lots))
        max_lots = (
            int(max_loss // one_lot_loss)
            if one_lot_loss > 0
            else 0
        )

        rc = st.columns(4)
        rc[0].metric("Allowed Risk", fmt_inr(max_loss))
        rc[1].metric("Scenario SL Loss", fmt_inr(conservative_loss))
        rc[2].metric(
            "Risk Buffer",
            fmt_inr(max_loss - conservative_loss),
        )
        rc[3].metric(
            "Suggested Max Lots",
            str(max_lots),
        )

        if conservative_loss <= max_loss:
            st.success("Current scenario is within your selected risk limit.")
        else:
            st.error("Current scenario exceeds your selected risk limit.")

# ---------- REFRESH ----------

st.divider()
if st.button("🔄 Refresh Live Data", use_container_width=True):
    st.rerun()
