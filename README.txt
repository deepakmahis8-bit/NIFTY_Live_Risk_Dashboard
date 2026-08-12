NIFTY ANGEL ONE RISK ENGINE — MPIN FIX

1. Replace app.py and requirements.txt in:
   C:\NIFTY_Live_Risk_Dashboard

2. CMD:
   cd /d C:\NIFTY_Live_Risk_Dashboard
   python -m pip install -r requirements.txt
   python -m streamlit run app.py

3. Login fields:
   API Key
   Client ID
   MPIN
   Current 6-digit TOTP

The app uses Angel One's MPIN login endpoint and is read-only.
Never send API keys, MPINs or TOTP codes in chat.
