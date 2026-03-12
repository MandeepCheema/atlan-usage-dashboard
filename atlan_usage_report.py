"""
Atlan Usage Report
------------------
Captures:
1. Login activity  - who logged in and how often
2. Asset viewers   - who viewed which assets and how many times
3. Most viewed     - top assets by views across the platform
4. Domain activity - view counts rolled up by domain
"""

import os
from datetime import datetime, timedelta
from collections import defaultdict

from pyatlan.client.atlan import AtlanClient
from pyatlan.model.enums import KeycloakEventType
from pyatlan.model.keycloak_events import KeycloakEventRequest
from pyatlan.model.search_log import SearchLogRequest
from pyatlan.model.fluent_search import FluentSearch
from pyatlan.model.assets import Asset

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

ATLAN_URL = os.environ.get("ATLAN_BASE_URL", "https://dsm.atlan.com")
ATLAN_API_KEY = os.environ.get("ATLAN_API_KEY", "")

# Report window — default last 30 days
DAYS_BACK = int(os.environ.get("REPORT_DAYS", 30))
DATE_FROM = (datetime.utcnow() - timedelta(days=DAYS_BACK)).strftime("%Y-%m-%d")
DATE_TO = datetime.utcnow().strftime("%Y-%m-%d")

# Users to exclude from all reports (bots, support accounts)
EXCLUDE_USERS = ["atlansupport", "service-account-apikey"]

# How many top assets / users to surface
TOP_N = 20

# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

client = AtlanClient(base_url=ATLAN_URL, api_key=ATLAN_API_KEY)


# ---------------------------------------------------------------------------
# 1. Login Activity
# ---------------------------------------------------------------------------

def get_login_activity():
    """Returns per-user login counts and last login time."""
    print(f"\n[1/5] Fetching login events ({DATE_FROM} → {DATE_TO}) ...")

    request = KeycloakEventRequest(
        date_from=DATE_FROM,
        date_to=DATE_TO,
    )

    login_counts = defaultdict(int)
    last_login = {}

    try:
        for event in client.admin.get_keycloak_events(request):
            event_type = getattr(event, "type", None)
            if event_type and event_type != KeycloakEventType.LOGIN:
                continue
            username = getattr(event, "user_id", None) or getattr(event, "username", "unknown")
            if any(ex in str(username) for ex in EXCLUDE_USERS):
                continue
            login_counts[username] += 1
            ts = getattr(event, "time", None)
            if ts:
                if username not in last_login or ts > last_login[username]:
                    last_login[username] = ts
    except Exception as e:
        print(f"  WARNING: Could not fetch login events: {e}")

    return login_counts, last_login


# ---------------------------------------------------------------------------
# 2. Most Viewed Assets (platform-wide)
# ---------------------------------------------------------------------------

def get_most_viewed_assets():
    """Returns top assets by total views and distinct user count."""
    print(f"\n[2/5] Fetching most viewed assets (top {TOP_N}) ...")

    request = SearchLogRequest.most_viewed_assets(
        max_assets=TOP_N,
        by_different_user=True,     # rank by distinct users
        exclude_users=EXCLUDE_USERS,
    )

    asset_views = []
    try:
        response = client.search_log.search(request)
        for detail in response.asset_views:
            asset_views.append({
                "guid": detail.guid,
                "total_views": detail.total_views,
                "distinct_users": detail.distinct_users,
            })
    except Exception as e:
        print(f"  WARNING: Could not fetch most viewed assets: {e}")

    return asset_views


# ---------------------------------------------------------------------------
# 3. Enrich asset GUIDs with names + domain info
# ---------------------------------------------------------------------------

def enrich_assets(asset_views):
    """Look up asset name, type, and domain for each GUID."""
    print(f"\n[3/5] Enriching {len(asset_views)} assets with metadata ...")

    guids = [a["guid"] for a in asset_views if a.get("guid")]
    if not guids:
        return asset_views

    guid_map = {}
    try:
        search_request = (
            FluentSearch()
            .where(FluentSearch.active_assets())
            .where(Asset.GUID.within(guids))
            .include_on_results(Asset.NAME)
            .include_on_results(Asset.TYPE_NAME)
            .include_on_results(Asset.QUALIFIED_NAME)
            .include_on_results(Asset.DOMAIN_GUIDS)
        ).to_request()

        for asset in client.asset.search(search_request):
            guid_map[asset.guid] = {
                "name": asset.name or asset.qualified_name,
                "type": asset.type_name,
                "domains": list(asset.domain_guids) if asset.domain_guids else [],
            }
    except Exception as e:
        print(f"  WARNING: Could not enrich assets: {e}")

    for a in asset_views:
        meta = guid_map.get(a["guid"], {})
        a["name"] = meta.get("name", a["guid"])
        a["type"] = meta.get("type", "Unknown")
        a["domains"] = meta.get("domains", [])

    return asset_views


# ---------------------------------------------------------------------------
# 4. Domain-level rollup
# ---------------------------------------------------------------------------

def resolve_domain_names(domain_guids):
    """Look up domain names from their GUIDs."""
    if not domain_guids:
        return {}

    from pyatlan.model.assets import DataDomain

    name_map = {}
    try:
        search_request = (
            FluentSearch()
            .where(FluentSearch.active_assets())
            .where(Asset.GUID.within(list(domain_guids)))
            .include_on_results(Asset.NAME)
        ).to_request()

        for asset in client.asset.search(search_request):
            name_map[asset.guid] = asset.name or asset.guid
    except Exception as e:
        print(f"  WARNING: Could not resolve domain names: {e}")

    return name_map


def rollup_by_domain(asset_views):
    """Aggregate total views and distinct users per domain, with names."""
    domain_stats = defaultdict(lambda: {"total_views": 0, "assets": set()})

    for asset in asset_views:
        for domain in asset.get("domains", []):
            domain_stats[domain]["total_views"] += asset.get("total_views", 0)
            domain_stats[domain]["assets"].add(asset["guid"])

    domain_guids = set(domain_stats.keys())
    domain_names = resolve_domain_names(domain_guids)

    result = []
    for domain_guid, stats in domain_stats.items():
        result.append({
            "domain_guid": domain_guid,
            "domain_name": domain_names.get(domain_guid, domain_guid),
            "total_views": stats["total_views"],
            "asset_count": len(stats["assets"]),
        })

    return sorted(result, key=lambda x: x["total_views"], reverse=True)


# ---------------------------------------------------------------------------
# Print Report
# ---------------------------------------------------------------------------

def print_report(login_counts, last_login, asset_views, domain_rollup):
    sep = "=" * 70

    print(f"\n{sep}")
    print(f"  ATLAN USAGE REPORT  |  {DATE_FROM} → {DATE_TO}")
    print(sep)

    # --- Login summary ---
    print(f"\n{'─'*70}")
    print(f"  LOGIN ACTIVITY  (last {DAYS_BACK} days)")
    print(f"{'─'*70}")
    print(f"  Total unique users who logged in: {len(login_counts)}")
    print(f"  Total login events:               {sum(login_counts.values())}")

    if login_counts:
        print(f"\n  Top {min(TOP_N, len(login_counts))} users by login count:")
        print(f"  {'Username':<40} {'Logins':>8}  Last Login")
        print(f"  {'-'*40} {'-'*8}  {'-'*20}")
        for user, count in sorted(login_counts.items(), key=lambda x: -x[1])[:TOP_N]:
            ts = last_login.get(user, "")
            if isinstance(ts, (int, float)):
                ts = datetime.utcfromtimestamp(ts / 1000).strftime("%Y-%m-%d %H:%M UTC")
            print(f"  {str(user):<40} {count:>8}  {ts}")

    # --- Most viewed assets ---
    print(f"\n{'─'*70}")
    print(f"  MOST VIEWED ASSETS  (top {TOP_N})")
    print(f"{'─'*70}")
    if asset_views:
        print(f"  {'Asset Name':<40} {'Type':<15} {'Views':>8}  {'Uniq Users':>10}")
        print(f"  {'-'*40} {'-'*15} {'-'*8}  {'-'*10}")
        for a in asset_views[:TOP_N]:
            print(f"  {str(a.get('name',''))[:40]:<40} {str(a.get('type',''))[:15]:<15} "
                  f"{a.get('total_views',0):>8}  {a.get('distinct_users',0):>10}")
    else:
        print("  No data available.")

    # --- Domain rollup ---
    print(f"\n{'─'*70}")
    print(f"  DOMAIN-LEVEL ACTIVITY ROLLUP")
    print(f"{'─'*70}")
    if domain_rollup:
        print(f"  {'Domain Name':<35} {'Assets':>8}  {'Total Views':>12}  GUID")
        print(f"  {'-'*35} {'-'*8}  {'-'*12}  {'-'*36}")
        for d in domain_rollup[:TOP_N]:
            print(f"  {str(d['domain_name'])[:35]:<35} {d['asset_count']:>8}  "
                  f"{d['total_views']:>12}  {d['domain_guid']}")
    else:
        print("  No domain data available (assets may not be linked to domains).")

    print(f"\n{sep}")
    print(f"  Report generated at {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    print(sep)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    login_counts, last_login = get_login_activity()
    asset_views = get_most_viewed_assets()
    asset_views = enrich_assets(asset_views)
    print("\n[4/5] Rolling up activity by domain ...")
    domain_rollup = rollup_by_domain(asset_views)
    print("\n[5/5] Building report ...")
    print_report(login_counts, last_login, asset_views, domain_rollup)
