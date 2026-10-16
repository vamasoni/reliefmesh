# ReliefMesh — fixed MVP

## Fix in this version
The incident seed rows now use an explicit 14-column insert, matching the SQLite schema. The resources seed insert is also corrected to use six placeholders plus the timestamp (7 columns).

## Setup
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:SARVAM_API_KEY="YOUR_REAL_KEY"
streamlit run app.py
```
On macOS/Linux:
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export SARVAM_API_KEY="YOUR_REAL_KEY"
streamlit run app.py
```

## Features
- Sarvam Saaras v3 audio transcription in code-mixed mode
- Sarvam-105B extraction of structured incident details
- Human review before incident creation
- SQLite-persisted incidents, inventory, and offline queue
- Resource matching, status tracking, CSV exports
- Demo fallback when no API key is set

All seed data is fictional. Offline mode simulates queueing while the Streamlit server is still running; it is not a true offline PWA. Priority scores are illustrative and not validated emergency protocols. This prototype is not connected to emergency services.
