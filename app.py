"""
Atlan Usage Dashboard
---------------------
Run with:
    streamlit run atlan_usage_dashboard.py
"""

import os
import re
from datetime import datetime, timedelta
from collections import defaultdict

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Atlan Usage Dashboard",
    page_icon="📊",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Sidebar — config
# ---------------------------------------------------------------------------

with st.sidebar:
    st.image("https://atlan.com/assets/img/atlan-blue.png", width=140)
    st.title("Configuration")

    atlan_url = st.text_input(
        "Atlan URL",
        value=os.environ.get("ATLAN_BASE_URL", "https://home.atlan.com"),
    )
    api_key = st.text_input(
        "API Key",
        value=os.environ.get("ATLAN_API_KEY", ""),
        type="password",
    )

    st.divider()
    st.caption("**Usage Activity**")
    days_back = st.slider("Report window (days)", min_value=7, max_value=90, value=30)
    top_n = st.slider("Top N assets / users", min_value=5, max_value=50, value=20)
    exclude_users_raw = st.text_input(
        "Exclude users (comma-separated)",
        value="atlansupport",
    )
    exclude_users = [u.strip() for u in exclude_users_raw.split(",") if u.strip()]

    if atlan_url and not atlan_url.startswith("https://"):
        st.warning("URL should start with `https://`")

    run_usage = st.button("▶ Run Usage Report", type="primary", use_container_width=True)
    run_maturity = st.button("▶ Run Maturity Report", type="secondary", use_container_width=True)
    run = run_usage or run_maturity

date_from = (datetime.utcnow() - timedelta(days=days_back)).strftime("%Y-%m-%d")
date_to = datetime.utcnow().strftime("%Y-%m-%d")

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

st.title("📊 Atlan Usage Dashboard")
st.caption(f"Reporting window: **{date_from}** → **{date_to}**")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def normalize_url(url):
    url = url.strip().rstrip("/")
    return re.sub(r'^(https?:)/([^/])', r'\1//\2', url)


def score_asset(asset):
    """
    Returns a dict of {dimension: bool} and an overall 0-100 score.
    Dimensions: description, owners, tags, terms, readme, lineage, certificate
    """
    dims = {}
    dims["Description"] = bool(
        getattr(asset, "user_description", None) or getattr(asset, "description", None)
    )
    dims["Owners"] = bool(
        getattr(asset, "owner_users", None) or getattr(asset, "owner_groups", None)
    )
    dims["Tags"] = bool(
        getattr(asset, "atlan_tags", None) or getattr(asset, "atlan_tag_names", None)
    )
    dims["Terms"] = bool(
        getattr(asset, "meanings", None) or getattr(asset, "assigned_terms", None)
    )
    dims["README"] = bool(getattr(asset, "readme", None))
    dims["Lineage"] = bool(getattr(asset, "has_lineage", False))
    dims["Certificate"] = bool(getattr(asset, "certificate_status", None))

    score = round(sum(dims.values()) / len(dims) * 100)
    return dims, score


MATURITY_DIMS = ["Description", "Owners", "Tags", "Terms", "README", "Lineage", "Certificate"]

MATURITY_COLORS = {
    "Excellent": "#22c55e",   # green   ≥80
    "Good":      "#84cc16",   # lime    60-79
    "Fair":      "#f59e0b",   # amber   40-59
    "Poor":      "#ef4444",   # red     <40
}

def maturity_band(score):
    if score >= 80: return "Excellent"
    if score >= 60: return "Good"
    if score >= 40: return "Fair"
    return "Poor"


# ---------------------------------------------------------------------------
# Data loaders (cached)
# ---------------------------------------------------------------------------

@st.cache_data(show_spinner=False, ttl=300)
def load_usage_data(atlan_url, api_key, date_from, date_to, top_n, exclude_users):
    from pyatlan.client.atlan import AtlanClient
    from pyatlan.model.enums import KeycloakEventType as KET
    from pyatlan.model.keycloak_events import KeycloakEventRequest
    from pyatlan.model.search_log import SearchLogRequest
    from pyatlan.model.fluent_search import FluentSearch
    from pyatlan.model.assets import Asset

    client = AtlanClient(base_url=normalize_url(atlan_url), api_key=api_key)
    errors = []
    exclude_users_list = list(exclude_users)

    # 0. User id → display name map
    user_id_map = {}
    try:
        PAGE = 100
        offset = 0
        while True:
            resp = client.user.get(limit=PAGE, offset=offset, count=True)
            page_users = list(resp.current_page())
            if not page_users:
                break
            for u in page_users:
                if not u.id:
                    continue
                if u.first_name or u.last_name:
                    display = f"{u.first_name or ''} {u.last_name or ''}".strip()
                    if u.email:
                        display += f" ({u.email})"
                else:
                    display = u.username or u.id
                user_id_map[u.id] = display
            if len(page_users) < PAGE:
                break
            offset += PAGE
    except Exception as e:
        errors.append(f"User lookup: {e}")

    # 1. Login events (paginated, capped)
    login_counts = defaultdict(int)
    last_login = {}
    try:
        request = KeycloakEventRequest(date_from=date_from, date_to=date_to, size=200, offset=0)
        response = client.admin.get_keycloak_events(request)
        page_num = 0
        while True:
            for event in response.current_page():
                if getattr(event, "type", None) != KET.LOGIN:
                    continue
                raw_id = getattr(event, "user_id", None) or "unknown"
                username = user_id_map.get(raw_id, raw_id)
                if any(ex in str(username) for ex in exclude_users_list):
                    continue
                login_counts[username] += 1
                ts = getattr(event, "time", None)
                if ts and (username not in last_login or ts > last_login[username]):
                    last_login[username] = ts
            page_num += 1
            if page_num >= 10 or not response.next_page():
                break
    except Exception as e:
        errors.append(f"Login events: {e}")

    # 2. Most viewed assets
    asset_views = []
    try:
        req = SearchLogRequest.most_viewed_assets(
            max_assets=top_n, by_different_user=True, exclude_users=exclude_users_list
        )
        resp = client.search_log.search(req)
        for detail in resp.asset_views:
            asset_views.append({
                "guid": detail.guid,
                "total_views": detail.total_views,
                "distinct_users": detail.distinct_users,
            })
    except Exception as e:
        errors.append(f"Most viewed assets: {e}")

    # 3. Enrich asset GUIDs with name/type/domain
    if asset_views:
        guids = [a["guid"] for a in asset_views if a.get("guid")]
        try:
            sr = (
                FluentSearch()
                .where(FluentSearch.active_assets())
                .where(Asset.GUID.within(guids))
                .include_on_results(Asset.NAME)
                .include_on_results(Asset.TYPE_NAME)
                .include_on_results(Asset.QUALIFIED_NAME)
                .include_on_results(Asset.DOMAIN_GUIDS)
            ).to_request()
            guid_map = {}
            for asset in client.asset.search(sr):
                raw_domains = getattr(asset, "domain_g_u_i_ds", None) or getattr(asset, "domain_guids", None)
                guid_map[asset.guid] = {
                    "name": asset.name or asset.qualified_name or asset.guid,
                    "type": asset.type_name or "Unknown",
                    "domains": list(raw_domains) if raw_domains else [],
                }
            for a in asset_views:
                meta = guid_map.get(a["guid"], {})
                a["name"] = meta.get("name", a["guid"])
                a["type"] = meta.get("type", "Unknown")
                a["domains"] = meta.get("domains", [])
        except Exception as e:
            errors.append(f"Asset enrichment: {e}")
            for a in asset_views:
                a.setdefault("name", a["guid"])
                a.setdefault("type", "Unknown")
                a.setdefault("domains", [])

    # 4. Domain rollup
    domain_stats = defaultdict(lambda: {"total_views": 0, "assets": set()})
    for asset in asset_views:
        for domain in asset.get("domains", []):
            domain_stats[domain]["total_views"] += asset.get("total_views", 0)
            domain_stats[domain]["assets"].add(asset["guid"])

    domain_names = {}
    if domain_stats:
        try:
            dr = (
                FluentSearch()
                .where(FluentSearch.active_assets())
                .where(Asset.GUID.within(list(domain_stats.keys())))
                .include_on_results(Asset.NAME)
            ).to_request()
            for asset in client.asset.search(dr):
                domain_names[asset.guid] = asset.name or asset.guid
        except Exception as e:
            errors.append(f"Domain name resolution: {e}")

    domain_rollup = sorted([
        {
            "domain_name": domain_names.get(guid, guid),
            "domain_guid": guid,
            "total_views": stats["total_views"],
            "asset_count": len(stats["assets"]),
        }
        for guid, stats in domain_stats.items()
    ], key=lambda x: x["total_views"], reverse=True)

    return login_counts, last_login, asset_views, domain_rollup, errors


@st.cache_data(show_spinner=False, ttl=300)
def load_maturity_data(atlan_url, api_key):
    from pyatlan.client.atlan import AtlanClient
    from pyatlan.model.fluent_search import FluentSearch
    from pyatlan.model.assets import Asset

    client = AtlanClient(base_url=normalize_url(atlan_url), api_key=api_key)
    errors = []
    SKIP_TYPES = {"DataDomain", "DataProduct"}

    def maturity_fields(fs):
        return (
            fs
            .include_on_results(Asset.NAME)
            .include_on_results(Asset.QUALIFIED_NAME)
            .include_on_results(Asset.TYPE_NAME)
            .include_on_results(Asset.USER_DESCRIPTION)
            .include_on_results(Asset.DESCRIPTION)
            .include_on_results(Asset.OWNER_USERS)
            .include_on_results(Asset.OWNER_GROUPS)
            .include_on_results(Asset.ATLAN_TAGS)
            .include_on_results(Asset.HAS_LINEAGE)
            .include_on_results(Asset.CERTIFICATE_STATUS)
            .include_on_results(Asset.DOMAIN_GUIDS)
            .include_on_results(Asset.README)
        )

    # 1. Fetch ALL domains (auto-paginates)
    domain_name_map = {}   # guid → display name
    domain_guids_all = []  # ordered list of all domain guids
    try:
        domain_req = maturity_fields(
            FluentSearch()
            .where(FluentSearch.active_assets())
            .where(Asset.TYPE_NAME.eq("DataDomain"))
        ).to_request()
        for d in client.asset.search(domain_req):
            domain_name_map[d.guid] = d.name or d.qualified_name or d.guid
            domain_guids_all.append(d.guid)
    except Exception as e:
        errors.append(f"Domain fetch: {e}")

    seen_guids = set()
    asset_records = []

    def record_asset(asset, domain_guids, domain_labels):
        if asset.guid in seen_guids:
            return
        if asset.type_name in SKIP_TYPES:
            return
        seen_guids.add(asset.guid)
        dims, score = score_asset(asset)
        asset_records.append({
            "guid": asset.guid,
            "name": asset.name or asset.qualified_name or asset.guid,
            "type": asset.type_name or "Unknown",
            "domain_guids": domain_guids,
            "domains": ", ".join(domain_labels) if domain_labels else "(No Domain)",
            "score": score,
            "band": maturity_band(score),
            **{f"has_{k}": v for k, v in dims.items()},
        })

    # 2. For every domain fetch ALL assets (auto-paginates)
    for domain_guid in domain_guids_all:
        domain_label = domain_name_map.get(domain_guid, domain_guid)
        try:
            req = maturity_fields(
                FluentSearch()
                .where(FluentSearch.active_assets())
                .where(Asset.DOMAIN_GUIDS.eq(domain_guid))
            ).to_request()
            req.dsl.size = 300  # max page size per request

            for asset in client.asset.search(req):
                raw_domains = getattr(asset, "domain_g_u_i_ds", None) or []
                d_guids = list(raw_domains)
                d_labels = [domain_name_map.get(g, g) for g in d_guids]
                record_asset(asset, d_guids, d_labels)
        except Exception as e:
            errors.append(f"Assets for domain '{domain_label}': {e}")

    # 3. Also fetch ALL assets with no domain
    try:
        all_req = maturity_fields(
            FluentSearch()
            .where(FluentSearch.active_assets())
        ).to_request()
        all_req.dsl.size = 300

        for asset in client.asset.search(all_req):
            raw_domains = getattr(asset, "domain_g_u_i_ds", None) or []
            if raw_domains:
                continue  # already captured above
            record_asset(asset, [], ["(No Domain)"])
    except Exception as e:
        errors.append(f"No-domain assets: {e}")

    return asset_records, domain_name_map, errors


# ---------------------------------------------------------------------------
# Session state — persist data across widget-triggered reruns
# ---------------------------------------------------------------------------

# Initialise session state keys so they always exist
for _key in ["usage_loaded", "maturity_loaded",
             "login_counts", "last_login", "asset_views", "domain_rollup", "usage_errors",
             "asset_records", "domain_name_map", "maturity_errors"]:
    if _key not in st.session_state:
        st.session_state[_key] = None if _key.endswith(("_map",)) else (
            [] if _key in ("asset_views", "domain_rollup", "asset_records",
                           "usage_errors", "maturity_errors") else
            {} if _key in ("login_counts", "last_login") else False
        )

# ---------------------------------------------------------------------------
# Validate connection inputs
# ---------------------------------------------------------------------------

if not atlan_url or not api_key:
    st.error("Please provide both Atlan URL and API Key in the sidebar.")
    st.stop()

# ---------------------------------------------------------------------------
# Load data when buttons are clicked
# ---------------------------------------------------------------------------

if run_usage:
    with st.spinner("Fetching usage data..."):
        (st.session_state.login_counts,
         st.session_state.last_login,
         st.session_state.asset_views,
         st.session_state.domain_rollup,
         st.session_state.usage_errors) = load_usage_data(
            atlan_url, api_key, date_from, date_to, top_n, tuple(exclude_users)
        )
    st.session_state.usage_loaded = True

if run_maturity:
    with st.spinner("Scoring metadata maturity..."):
        (st.session_state.asset_records,
         st.session_state.domain_name_map,
         st.session_state.maturity_errors) = load_maturity_data(
            atlan_url, api_key
        )
    st.session_state.maturity_loaded = True

# ---------------------------------------------------------------------------
# Guard — show prompt if nothing loaded yet
# ---------------------------------------------------------------------------

if not st.session_state.usage_loaded and not st.session_state.maturity_loaded:
    st.info("Configure your Atlan connection in the sidebar and click a **Run** button.")
    st.stop()

# ---------------------------------------------------------------------------
# Pull data from session state for rendering
# ---------------------------------------------------------------------------

login_counts    = st.session_state.login_counts or {}
last_login      = st.session_state.last_login or {}
asset_views     = st.session_state.asset_views or []
domain_rollup   = st.session_state.domain_rollup or []
asset_records   = st.session_state.asset_records or []
domain_name_map = st.session_state.domain_name_map or {}

all_errors = (st.session_state.usage_errors or []) + (st.session_state.maturity_errors or [])
if all_errors:
    with st.expander("⚠️ Some data could not be loaded", expanded=False):
        for e in all_errors:
            st.warning(e)

# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------

tab_usage, tab_maturity = st.tabs(["📈 Usage Activity", "🏆 Metadata Maturity"])


# ===========================================================================
# TAB 1 — USAGE ACTIVITY
# ===========================================================================

with tab_usage:

    # KPI row
    total_logins = sum(login_counts.values())
    unique_users = len(login_counts)
    total_asset_views = sum(a.get("total_views", 0) for a in asset_views)
    total_domains_active = len(domain_rollup)

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Total Logins", f"{total_logins:,}", help=f"Last {days_back} days")
    k2.metric("Unique Users Logged In", f"{unique_users:,}")
    k3.metric("Asset Views (top assets)", f"{total_asset_views:,}")
    k4.metric("Active Domains", f"{total_domains_active:,}")

    st.divider()

    # Login activity
    st.subheader("🔐 Login Activity")
    if login_counts:
        login_df = pd.DataFrame([
            {
                "Username": user,
                "Logins": count,
                "Last Login": (
                    datetime.utcfromtimestamp(last_login[user] / 1000).strftime("%Y-%m-%d %H:%M UTC")
                    if user in last_login else "—"
                ),
            }
            for user, count in sorted(login_counts.items(), key=lambda x: -x[1])
        ])
        col_chart, col_table = st.columns([1.4, 1])
        with col_chart:
            fig = px.bar(
                login_df.head(top_n), x="Logins", y="Username", orientation="h",
                title=f"Top {top_n} Users by Login Count",
                color="Logins", color_continuous_scale="Blues",
            )
            fig.update_layout(yaxis={"categoryorder": "total ascending"}, showlegend=False, height=420)
            st.plotly_chart(fig, use_container_width=True)
        with col_table:
            st.dataframe(login_df, use_container_width=True, hide_index=True, height=420)
    else:
        st.info("No login events found for the selected window.")

    st.divider()

    # Most viewed assets
    st.subheader("👁️ Most Viewed Assets")
    if asset_views:
        assets_df = pd.DataFrame(asset_views)[["name", "type", "total_views", "distinct_users", "guid"]]
        assets_df.columns = ["Asset Name", "Type", "Total Views", "Distinct Users", "GUID"]
        col_bar, col_scatter = st.columns(2)
        with col_bar:
            fig = px.bar(
                assets_df.head(top_n), x="Total Views", y="Asset Name", orientation="h",
                color="Type", title="Top Assets by Total Views",
            )
            fig.update_layout(yaxis={"categoryorder": "total ascending"}, height=420)
            st.plotly_chart(fig, use_container_width=True)
        with col_scatter:
            fig = px.scatter(
                assets_df, x="Distinct Users", y="Total Views", text="Asset Name",
                color="Type", size="Total Views", title="Views vs Distinct Users",
                hover_data=["GUID"],
            )
            fig.update_traces(textposition="top center")
            fig.update_layout(height=420)
            st.plotly_chart(fig, use_container_width=True)
        st.dataframe(assets_df, use_container_width=True, hide_index=True)
    else:
        st.info("No asset view data found.")

    st.divider()

    # Domain activity
    st.subheader("🗂️ Domain-Level Activity")
    if domain_rollup:
        domains_df = pd.DataFrame(domain_rollup)[["domain_name", "asset_count", "total_views", "domain_guid"]]
        domains_df.columns = ["Domain Name", "Assets Viewed", "Total Views", "GUID"]
        col_pie, col_bar2 = st.columns([1, 1.2])
        with col_pie:
            fig = px.pie(domains_df, names="Domain Name", values="Total Views",
                         title="Share of Views by Domain", hole=0.4)
            fig.update_layout(height=380)
            st.plotly_chart(fig, use_container_width=True)
        with col_bar2:
            fig = px.bar(domains_df, x="Domain Name", y="Total Views",
                         color="Assets Viewed", color_continuous_scale="Teal",
                         title="Total Views per Domain")
            fig.update_layout(height=380)
            st.plotly_chart(fig, use_container_width=True)
        st.dataframe(domains_df, use_container_width=True, hide_index=True)
    else:
        st.info("No domain activity found (assets may not be linked to domains yet).")


# ===========================================================================
# TAB 2 — METADATA MATURITY
# ===========================================================================

with tab_maturity:

    if not asset_records:
        st.info("No asset maturity data available.")
        st.stop()

    maturity_df = pd.DataFrame(asset_records)

    # ── KPIs ────────────────────────────────────────────────────────────────
    avg_score = round(maturity_df["score"].mean(), 1)
    pct_excellent = round((maturity_df["score"] >= 80).mean() * 100, 1)
    pct_poor = round((maturity_df["score"] < 40).mean() * 100, 1)
    total_scored = len(maturity_df)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Assets Scored", f"{total_scored:,}")
    m2.metric("Avg Maturity Score", f"{avg_score}%")
    m3.metric("Excellent (≥80%)", f"{pct_excellent}%")
    m4.metric("Poor (<40%)", f"{pct_poor}%")

    st.divider()

    # ── Overall dimension completeness ──────────────────────────────────────
    st.subheader("📋 Overall Metadata Completeness")

    dim_pcts = {
        dim: round(maturity_df[f"has_{dim}"].mean() * 100, 1)
        for dim in MATURITY_DIMS
    }
    dim_df = pd.DataFrame({
        "Dimension": list(dim_pcts.keys()),
        "% Assets with Field": list(dim_pcts.values()),
    }).sort_values("% Assets with Field", ascending=True)

    col_dim, col_band = st.columns([1.4, 1])

    with col_dim:
        fig = px.bar(
            dim_df, x="% Assets with Field", y="Dimension", orientation="h",
            title="% of Assets with Each Metadata Field",
            color="% Assets with Field",
            color_continuous_scale=["#ef4444", "#f59e0b", "#22c55e"],
            range_color=[0, 100],
            text="% Assets with Field",
        )
        fig.update_traces(texttemplate="%{text}%", textposition="outside")
        fig.update_layout(height=360, showlegend=False, coloraxis_showscale=False)
        st.plotly_chart(fig, use_container_width=True)

    with col_band:
        band_counts = maturity_df["band"].value_counts().reset_index()
        band_counts.columns = ["Band", "Count"]
        band_order = ["Excellent", "Good", "Fair", "Poor"]
        band_counts["Band"] = pd.Categorical(band_counts["Band"], categories=band_order, ordered=True)
        band_counts = band_counts.sort_values("Band")
        fig = px.pie(
            band_counts, names="Band", values="Count",
            title="Assets by Maturity Band",
            color="Band",
            color_discrete_map=MATURITY_COLORS,
            hole=0.45,
        )
        fig.update_layout(height=360)
        st.plotly_chart(fig, use_container_width=True)

    st.divider()

    # ── Domain-level maturity heatmap ───────────────────────────────────────
    st.subheader("🗂️ Domain-Level Maturity")

    # Explode assets per domain (an asset can belong to multiple domains)
    domain_rows = []
    for _, row in maturity_df.iterrows():
        domains = row["domain_guids"] if row["domain_guids"] else ["(No Domain)"]
        for dg in domains:
            domain_label = domain_name_map.get(dg, dg) if dg != "(No Domain)" else "(No Domain)"
            domain_rows.append({
                "domain": domain_label,
                "score": row["score"],
                **{dim: row[f"has_{dim}"] for dim in MATURITY_DIMS},
            })

    domain_exploded = pd.DataFrame(domain_rows)

    # Per-domain avg score + dimension breakdown
    domain_summary = domain_exploded.groupby("domain").agg(
        avg_score=("score", "mean"),
        asset_count=("score", "count"),
        **{dim: (dim, "mean") for dim in MATURITY_DIMS}
    ).reset_index()
    domain_summary["avg_score"] = domain_summary["avg_score"].round(1)
    domain_summary = domain_summary.sort_values("avg_score", ascending=False)

    # Bar chart — avg score per domain
    col_dscore, col_dcount = st.columns([1.5, 1])
    with col_dscore:
        fig = px.bar(
            domain_summary.head(20),
            x="avg_score", y="domain", orientation="h",
            title="Average Maturity Score by Domain",
            color="avg_score",
            color_continuous_scale=["#ef4444", "#f59e0b", "#22c55e"],
            range_color=[0, 100],
            text="avg_score",
        )
        fig.update_traces(texttemplate="%{text}%", textposition="outside")
        fig.update_layout(
            yaxis={"categoryorder": "total ascending"},
            height=max(300, len(domain_summary) * 35),
            coloraxis_showscale=False,
        )
        st.plotly_chart(fig, use_container_width=True)

    with col_dcount:
        fig = px.bar(
            domain_summary.head(20),
            x="asset_count", y="domain", orientation="h",
            title="Assets Scored per Domain",
            color="asset_count", color_continuous_scale="Blues",
        )
        fig.update_layout(
            yaxis={"categoryorder": "total ascending"},
            height=max(300, len(domain_summary) * 35),
        )
        st.plotly_chart(fig, use_container_width=True)

    # Heatmap — domain × dimension
    st.subheader("🔥 Metadata Completeness Heatmap (Domain × Dimension)")
    st.caption("Each cell = % of assets in that domain that have the metadata field populated.")

    heatmap_data = domain_summary.set_index("domain")[MATURITY_DIMS].multiply(100).round(1)

    fig = go.Figure(data=go.Heatmap(
        z=heatmap_data.values.tolist(),
        x=MATURITY_DIMS,
        y=heatmap_data.index.tolist(),
        colorscale=[
            [0.0, "#ef4444"],
            [0.4, "#f59e0b"],
            [0.8, "#22c55e"],
            [1.0, "#16a34a"],
        ],
        zmin=0, zmax=100,
        text=[[f"{v:.0f}%" for v in row] for row in heatmap_data.values],
        texttemplate="%{text}",
        textfont={"size": 11},
        hoverongaps=False,
    ))
    fig.update_layout(
        height=max(300, len(heatmap_data) * 40 + 100),
        xaxis_title="Metadata Dimension",
        yaxis_title="Domain",
        margin={"l": 180},
    )
    st.plotly_chart(fig, use_container_width=True)

    st.divider()

    # ── Asset drilldown ─────────────────────────────────────────────────────
    st.subheader("🔍 Asset-Level Maturity Drilldown")

    # Filters
    f1, f2, f3 = st.columns(3)
    with f1:
        domain_options = ["All"] + sorted(maturity_df["domains"].unique().tolist())
        selected_domain = st.selectbox("Filter by Domain", domain_options)
    with f2:
        type_options = ["All"] + sorted(maturity_df["type"].unique().tolist())
        selected_type = st.selectbox("Filter by Asset Type", type_options)
    with f3:
        band_options = ["All", "Excellent", "Good", "Fair", "Poor"]
        selected_band = st.selectbox("Filter by Maturity Band", band_options)

    filtered = maturity_df.copy()
    if selected_domain != "All":
        filtered = filtered[filtered["domains"].str.contains(selected_domain, na=False)]
    if selected_type != "All":
        filtered = filtered[filtered["type"] == selected_type]
    if selected_band != "All":
        filtered = filtered[filtered["band"] == selected_band]

    # Build display table with coloured score + tick/cross per dimension
    display_cols = ["name", "type", "domains", "score", "band"] + [f"has_{d}" for d in MATURITY_DIMS]
    display_df = filtered[display_cols].copy()
    display_df.columns = (
        ["Asset Name", "Type", "Domains", "Score", "Band"]
        + MATURITY_DIMS
    )
    # Convert booleans to readable symbols
    for dim in MATURITY_DIMS:
        display_df[dim] = display_df[dim].map({True: "✅", False: "❌"})

    display_df = display_df.sort_values("Score", ascending=False)

    st.caption(f"Showing {len(display_df):,} of {len(maturity_df):,} assets")
    st.dataframe(
        display_df,
        use_container_width=True,
        hide_index=True,
        height=500,
        column_config={
            "Score": st.column_config.ProgressColumn(
                "Score", min_value=0, max_value=100, format="%d%%"
            ),
        },
    )

    # Download
    csv = display_df.to_csv(index=False)
    st.download_button(
        "⬇️ Download as CSV",
        data=csv,
        file_name=f"atlan_maturity_{date_to}.csv",
        mime="text/csv",
    )

# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------

st.divider()
st.caption(
    f"Report generated at {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')} · "
    f"Data cached for 5 minutes"
)
