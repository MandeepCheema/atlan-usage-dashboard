# Atlan Usage & Metadata Maturity Dashboard

A Streamlit dashboard that connects to your [Atlan](https://atlan.com) instance and shows:

- **Usage Activity** — who is logging in, which assets are most viewed, domain-level activity
- **Metadata Maturity** — how complete metadata is across all assets and domains (description, owners, tags, terms, README, lineage, certificate)

## Live Demo

[![Open in Streamlit](https://static.streamlit.io/badges/streamlit_badge_black_white.svg)](https://atlan-usage-dashboard.streamlit.app)

---

## Running Locally

### 1. Clone the repo

```bash
git clone https://github.com/MandeepCheema/atlan-usage-dashboard.git
cd atlan-usage-dashboard
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Run the app

```bash
streamlit run app.py
```

Open [http://localhost:8501](http://localhost:8501) in your browser.

### 4. Connect to your Atlan instance

In the sidebar enter:
- **Atlan URL** — e.g. `https://your-tenant.atlan.com`
- **API Key** — generate one from Atlan → Settings → API Keys

Then click **▶ Run Usage Report** or **▶ Run Maturity Report**.

---

## Deploying to Railway (recommended, free tier available)

> **Note:** Streamlit Community Cloud currently runs Python 3.14 which is incompatible with `pyatlan`'s internal use of `pydantic.v1`. Use Railway instead — it respects the `.python-version` file and runs Python 3.11.

1. Go to [railway.app](https://railway.app) and sign in with GitHub
2. Click **New Project** → **Deploy from GitHub repo**
3. Select `MandeepCheema/atlan-usage-dashboard`
4. Railway auto-detects the `Procfile` and `nixpacks.toml` — no config needed
5. Click **Deploy** → get a public URL like `https://atlan-usage-dashboard.up.railway.app`

No secrets needed — Atlan credentials are entered at runtime in the sidebar.

## Deploying to Streamlit Community Cloud

> ⚠️ Currently broken due to Python 3.14 / pydantic.v1 incompatibility on Streamlit Cloud.
> Use Railway above until Streamlit Cloud supports Python version pinning again.

---

## CLI Report (no UI)

You can also run the report as a plain CLI script:

```bash
ATLAN_BASE_URL="https://your-tenant.atlan.com" \
ATLAN_API_KEY="your-api-key" \
REPORT_DAYS=30 \
python atlan_usage_report.py
```

---

## What is scored in Metadata Maturity

| Dimension   | What is checked                                      |
|-------------|------------------------------------------------------|
| Description | `user_description` or `description` is set          |
| Owners      | At least one owner user or group is assigned        |
| Tags        | At least one Atlan tag is applied                   |
| Terms       | At least one business term (meaning) is linked      |
| README      | A README is attached to the asset                   |
| Lineage     | Asset has upstream or downstream lineage            |
| Certificate | Asset has a certification status set                |

Scores: **Excellent** ≥80% · **Good** 60–79% · **Fair** 40–59% · **Poor** <40%

---

## Requirements

- Python 3.9+
- An Atlan tenant with API access
- API key with admin or viewer permissions
