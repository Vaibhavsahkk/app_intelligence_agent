"""Pattern analysis and clustering for Composio app research results."""
import sys
import os
import json
import logging
from collections import defaultdict, Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import DATA_DIR

logger = logging.getLogger("composio_agent.analyzer")


def _is_self_serve(self_serve_value: str) -> bool:
    """Determine if an app is self-serve based on the self_serve field."""
    if not self_serve_value:
        return False
    lower = self_serve_value.lower()
    # These are self-serve
    if any(x in lower for x in ["self-serve", "self serve", "free", "trial", "open source", "no auth"]):
        return True
    # These are gated
    if any(x in lower for x in ["gated", "partner", "contact", "admin approval", "sales"]):
        return False
    # Default: if it contains "paid" but not "contact", still self-serve (paid plan, not gated)
    if "paid" in lower:
        return True
    return False


def run_analysis(results_file: str = None) -> dict:
    """Load research results, compute statistics, cluster apps, and generate insights.

    Args:
        results_file: Path to results JSON. If None, looks for final_results.json,
                      pass3_corrected.json, or pass1_results.json (in that order).

    Returns:
        Dict with patterns, clusters, and insights. Also saved to data/patterns.json.
    """
    # Find results file
    if not results_file:
        candidates = [
            os.path.join(DATA_DIR, "final_results.json"),
            os.path.join(DATA_DIR, "pass3_corrected.json"),
            os.path.join(DATA_DIR, "pass1_results.json"),
        ]
        for path in candidates:
            if os.path.exists(path):
                results_file = path
                break
        if not results_file:
            logger.error("No results file found in data/")
            return {}

    logger.info(f"Loading results from {results_file}")
    with open(results_file, "r", encoding="utf-8") as f:
        apps = json.load(f)

    if not isinstance(apps, list):
        apps = apps.get("apps", [])

    total_apps = len(apps)
    if total_apps == 0:
        logger.warning("No apps in results")
        return {}

    logger.info(f"Analyzing {total_apps} apps")

    # ---- Counters ----
    auth_counter = Counter()
    self_serve_data = {
        "overall": {"self_serve": 0, "gated": 0, "unknown": 0},
        "by_category": defaultdict(lambda: {"self_serve": 0, "gated": 0})
    }
    api_type_counter = Counter()
    mcp_count = 0
    webhook_count = 0
    sandbox_count = 0
    sdk_counter = Counter()
    blocker_counter = Counter()
    blocker_apps = defaultdict(list)
    confidence_counter = Counter()

    clusters = {
        "ready_today": [],
        "quick_win": [],
        "needs_outreach": [],
        "not_feasible": []
    }

    for app in apps:
        # Use the CORRECT field names from researcher.py output
        app_id = app.get("app_id", 0)
        name = app.get("app_name", f"App {app_id}")
        category = app.get("category", "Unknown")
        confidence = app.get("confidence", "UNKNOWN")
        confidence_counter[confidence] += 1

        # Auth methods (list field from researcher)
        auth_methods = app.get("auth_methods", [])
        if isinstance(auth_methods, str):
            auth_methods = [auth_methods]
        for method in auth_methods:
            if method and method != "Unknown":
                auth_counter[method] += 1

        # Self-serve vs gated
        self_serve_val = app.get("self_serve", "")
        is_ss = _is_self_serve(self_serve_val)
        if self_serve_val:
            if is_ss:
                self_serve_data["overall"]["self_serve"] += 1
                self_serve_data["by_category"][category]["self_serve"] += 1
            else:
                self_serve_data["overall"]["gated"] += 1
                self_serve_data["by_category"][category]["gated"] += 1
        else:
            self_serve_data["overall"]["unknown"] += 1

        # API type
        api_type = app.get("api_type", "")
        if api_type and api_type != "Unknown":
            api_type_counter[api_type] += 1

        # MCP
        if app.get("has_mcp", False):
            mcp_count += 1

        # Webhooks
        if app.get("has_webhooks", False):
            webhook_count += 1

        # Sandbox
        if app.get("has_sandbox", False):
            sandbox_count += 1

        # SDK languages
        sdk_langs = app.get("sdk_languages", [])
        if isinstance(sdk_langs, str):
            sdk_langs = [sdk_langs]
        for lang in sdk_langs:
            if lang and lang not in ("Unknown", "None found", "None"):
                sdk_counter[lang] += 1

        # Blockers
        blocker = app.get("main_blocker", "")
        if blocker and blocker not in ("None", "No blocker", "N/A", ""):
            blocker_counter[blocker] += 1
            blocker_apps[blocker].append(name)

        # Buildability and clustering
        buildability = app.get("buildability", "Unknown").lower()
        has_public_api = bool(api_type and api_type != "Unknown"
                             and "no " not in api_type.lower()
                             and "none" not in api_type.lower())

        app_summary = {"id": app_id, "name": name, "category": category}

        if "not feasible" in buildability or "not_feasible" in buildability or not has_public_api:
            clusters["not_feasible"].append(app_summary)
        elif not is_ss and has_public_api:
            clusters["needs_outreach"].append(app_summary)
        elif "ready" in buildability:
            clusters["ready_today"].append(app_summary)
        elif "minor" in buildability:
            clusters["quick_win"].append(app_summary)
        elif "significant" in buildability:
            clusters["needs_outreach"].append(app_summary)
        else:
            clusters["not_feasible"].append(app_summary)

    # ---- Compute distributions ----
    auth_distribution = {}
    for method, count in auth_counter.most_common():
        auth_distribution[method] = {
            "count": count,
            "percentage": round((count / total_apps) * 100, 1)
        }

    api_type_distribution = {}
    for api, count in api_type_counter.most_common():
        api_type_distribution[api] = {
            "count": count,
            "percentage": round((count / total_apps) * 100, 1)
        }

    sdk_distribution = {}
    for lang, count in sdk_counter.most_common():
        sdk_distribution[lang] = count

    top_blockers = []
    for blocker, count in blocker_counter.most_common(5):
        top_blockers.append({
            "blocker": blocker,
            "count": count,
            "apps": blocker_apps[blocker][:5]  # limit to 5 example apps
        })

    # ---- Insights ----
    # Most agent-ready category
    category_totals = Counter(app.get("category", "Unknown") for app in apps)
    cat_ready_count = Counter(a["category"] for a in clusters["ready_today"])

    most_agent_ready = "Unknown"
    max_ready_pct = 0.0
    for cat, total in category_totals.items():
        if total > 0:
            pct = (cat_ready_count.get(cat, 0) / total) * 100
            if pct > max_ready_pct:
                max_ready_pct = pct
                most_agent_ready = cat

    # Auth correlation with self-serve
    oauth_self = 0
    oauth_total = 0
    for app in apps:
        auth_list = app.get("auth_methods", [])
        if isinstance(auth_list, str):
            auth_list = [auth_list]
        has_oauth = any("oauth" in m.lower() for m in auth_list if m)
        if has_oauth:
            oauth_total += 1
            if _is_self_serve(app.get("self_serve", "")):
                oauth_self += 1

    if oauth_total > 0:
        oauth_self_pct = round((oauth_self / oauth_total) * 100, 1)
        overall_self_pct = round((self_serve_data["overall"]["self_serve"] / total_apps) * 100, 1)
        auth_corr = f"OAuth2 apps are {oauth_self_pct}% self-serve vs {overall_self_pct}% overall"
    else:
        auth_corr = "Not enough OAuth2 data for correlation"

    # OAuth percentage for headline
    oauth_pct = 0.0
    for method, stats in auth_distribution.items():
        if "oauth" in method.lower():
            oauth_pct += stats["percentage"]

    # Headline findings
    headline_findings = [
        f"{oauth_pct:.0f}% of apps use OAuth2 as their primary auth method",
        f"{most_agent_ready} is the most agent-ready category with {max_ready_pct:.0f}% ready today",
        f"The #1 blocker to buildability is: {top_blockers[0]['blocker'] if top_blockers else 'insufficient documentation'}",
        f"{mcp_count} out of {total_apps} apps already have MCP servers"
    ]

    insights = {
        "most_agent_ready_category": most_agent_ready,
        "most_agent_ready_pct": round(max_ready_pct, 1),
        "auth_self_serve_correlation": auth_corr,
        "easy_wins": [a["name"] for a in clusters["quick_win"]],
        "outreach_priority": [a["name"] for a in clusters["needs_outreach"]],
        "confidence_summary": dict(confidence_counter)
    }

    patterns = {
        "total_apps": total_apps,
        "auth_distribution": auth_distribution,
        "self_serve_breakdown": {
            "overall": dict(self_serve_data["overall"]),
            "by_category": {k: dict(v) for k, v in self_serve_data["by_category"].items()}
        },
        "api_type_distribution": api_type_distribution,
        "mcp_count": mcp_count,
        "webhook_count": webhook_count,
        "webhook_percentage": round((webhook_count / total_apps) * 100, 1) if total_apps else 0,
        "sandbox_count": sandbox_count,
        "sdk_distribution": sdk_distribution,
        "top_blockers": top_blockers,
        "clusters": {
            "ready_today": clusters["ready_today"],
            "quick_win": clusters["quick_win"],
            "needs_outreach": clusters["needs_outreach"],
            "not_feasible": clusters["not_feasible"]
        },
        "cluster_counts": {
            "ready_today": len(clusters["ready_today"]),
            "quick_win": len(clusters["quick_win"]),
            "needs_outreach": len(clusters["needs_outreach"]),
            "not_feasible": len(clusters["not_feasible"])
        },
        "insights": insights,
        "headline_findings": headline_findings
    }

    # Save patterns
    out_file = os.path.join(DATA_DIR, "patterns.json")
    os.makedirs(os.path.dirname(out_file), exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(patterns, f, indent=2, ensure_ascii=False)

    logger.info(f"Analysis complete. Saved to {out_file}")
    logger.info(f"Clusters: Ready={len(clusters['ready_today'])}, "
                f"QuickWin={len(clusters['quick_win'])}, "
                f"Outreach={len(clusters['needs_outreach'])}, "
                f"NotFeasible={len(clusters['not_feasible'])}")
    for h in headline_findings:
        logger.info(f"  Headline: {h}")

    return patterns


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_analysis()
