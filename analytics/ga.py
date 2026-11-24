"""GA4 Data API reports for every digital-object page.

Rather than querying per item, each report pulls *all* pages under the
configured prefix in one paginated request; joining to collections happens
locally. Credentials come from Application Default Credentials
(GOOGLE_APPLICATION_CREDENTIALS, `gcloud auth application-default login`,
or google-github-actions/auth in CI).
"""

from __future__ import annotations

import random
import re
from datetime import date, timedelta

PAGE_SIZE = 100_000

# Per-page reports: name -> (extra dimensions, metrics, eventName filter or None).
# Every one is keyed by hostName + pagePath and limited to "detail" pages:
# digital objects, pretty collection URLs and full-detail page groups.
PAGE_REPORTS: dict[str, tuple[list[str], list[str], str | None]] = {
    "items": ([], ["screenPageViews", "activeUsers", "userEngagementDuration"], None),
    "monthly": (["yearMonth"], ["screenPageViews"], None),
    "traffic": (["sessionDefaultChannelGroup", "sessionSource", "sessionMedium"], ["screenPageViews"], None),
    "geo": (["country"], ["screenPageViews"], None),
    # Enhanced-measurement file downloads, attributed to the page they happened on.
    "downloads": ([], ["eventCount"], "file_download"),
}
GROUP_METRICS = ["screenPageViews", "activeUsers", "userEngagementDuration"]


def path_regex(paths: list[str]) -> str:
    """RE2 pattern matching each path and anything below it (trailing slash optional)."""
    alts = sorted({re.escape(p.rstrip("/") or "/") for p in paths})
    return f"({'|'.join(alts)})(/.*)?"


def run_reports(property_id: str, start: str, end: str, hostnames: list[str], detail_paths: list[str],
                do_prefix: str, groups: dict[str, tuple[str | None, list[str]]], basic_paths: list[str]) -> dict[str, list[dict]]:
    """Run every report. Request count = 5 page reports + 1 site-wide /do/ by month
    + 1 per page group (de-duplicated users) + 1 for basic-group page totals."""
    from google.analytics.data_v1beta import BetaAnalyticsDataClient
    from google.analytics.data_v1beta.types import (
        DateRange, Dimension, Filter, FilterExpression, FilterExpressionList, Metric,
        MetricAggregation, RunReportRequest,
    )

    client = BetaAnalyticsDataClient()

    def regex(field: str, pattern: str) -> FilterExpression:
        return FilterExpression(filter=Filter(field_name=field, string_filter=Filter.StringFilter(
            match_type=Filter.StringFilter.MatchType.FULL_REGEXP, value=pattern)))

    def both(*exprs) -> FilterExpression:
        return FilterExpression(and_group=FilterExpressionList(expressions=[e for e in exprs if e]))

    host_f = FilterExpression(filter=Filter(field_name="hostName", in_list_filter=Filter.InListFilter(values=hostnames))) if hostnames else None

    def run(name, dims, mets, flt, totals=False):
        rows, offset = [], 0
        while True:
            resp = client.run_report(RunReportRequest(
                property=f"properties/{property_id}",
                date_ranges=[DateRange(start_date=start, end_date=end)],
                dimensions=[Dimension(name=d) for d in dims],
                metrics=[Metric(name=m) for m in mets],
                dimension_filter=flt,
                metric_aggregations=[MetricAggregation.TOTAL] if totals else [],
                limit=PAGE_SIZE, offset=offset,
            ))
            for r in resp.rows:
                row = {d: v.value for d, v in zip(dims, r.dimension_values)}
                row.update({m: float(v.value) for m, v in zip(mets, r.metric_values)})
                rows.append(row)
            if totals and resp.totals:
                # GA computes totals over the whole query, so users here are de-duplicated.
                out[f"{name}:total"] = [{m: float(v.value) for m, v in zip(mets, resp.totals[0].metric_values)}]
            offset += PAGE_SIZE
            if offset >= resp.row_count:
                break
        print(f"  GA report {name}: {len(rows)} rows")
        out[name] = rows

    out: dict[str, list[dict]] = {}
    detail_f = both(host_f, regex("pagePath", path_regex(detail_paths)))
    for name, (dims, mets, event) in PAGE_REPORTS.items():
        flt = detail_f
        if event:
            flt = both(detail_f, FilterExpression(filter=Filter(field_name="eventName", string_filter=Filter.StringFilter(value=event))))
        run(name, ["hostName", "pagePath", *dims], mets, flt)
    # Whole /do/ section by month, so site-wide users are de-duplicated by GA.
    run("site_monthly", ["yearMonth"], ["screenPageViews", "activeUsers"], both(host_f, regex("pagePath", path_regex([do_prefix]))), totals=True)
    for gid, (ghost, gpaths) in groups.items():
        ghost_f = FilterExpression(filter=Filter(field_name="hostName", string_filter=Filter.StringFilter(value=ghost))) if ghost else host_f
        run(f"group:{gid}", ["yearMonth"], GROUP_METRICS, both(ghost_f, regex("pagePath", path_regex(gpaths))), totals=True)
    if basic_paths:
        run("basic_pages", ["hostName", "pagePath"], ["screenPageViews", "activeUsers"],
            both(host_f, regex("pagePath", path_regex(basic_paths))))
    return out


# --------------------------------------------------------------------- sample
def sample_reports(uuids: list[str], prefix: str, host: str, months: int = 18, seed: int = 7) -> dict[str, list[dict]]:
    """Plausible fake data for developing the dashboard without GA access."""
    rng = random.Random(seed)
    today = date.today().replace(day=1)
    month_keys = []
    d = today
    for _ in range(months):
        d = (d - timedelta(days=1)).replace(day=1)
        month_keys.append(d.strftime("%Y%m"))
    month_keys.reverse()
    traffic_mix = [
        ("Organic Search", "google", "organic", 0.45),
        ("Direct", "(direct)", "(none)", 0.2),
        ("Referral", "library.tamu.edu", "referral", 0.12),
        ("Referral", "en.wikipedia.org", "referral", 0.06),
        ("Organic Search", "bing", "organic", 0.05),
        ("Organic Social", "facebook.com", "referral", 0.04),
        ("Referral", "archive.org", "referral", 0.03),
        ("Referral", "canvas.tamu.edu", "referral", 0.03),
        ("Email", "newsletter", "email", 0.02),
    ]
    countries = [("United States", 0.72), ("United Kingdom", 0.05), ("Canada", 0.04), ("Germany", 0.04),
                 ("France", 0.03), ("India", 0.03), ("Australia", 0.02), ("Japan", 0.02), ("Mexico", 0.02),
                 ("Netherlands", 0.01)]
    out = {k: [] for k in [*PAGE_REPORTS, "site_monthly"]}
    for uuid in uuids:
        if rng.random() < 0.12:  # some items never get traffic
            continue
        path = f"{prefix}{uuid}"
        hp = {"hostName": host, "pagePath": path}
        weight = rng.paretovariate(1.3)
        total = 0
        for i, m in enumerate(month_keys):
            v = int(weight * rng.uniform(2, 12) * (1 + i / months))
            if v:
                out["monthly"].append({**hp, "yearMonth": m, "screenPageViews": v})
                total += v
        if not total:
            continue
        out["items"].append({**hp, "screenPageViews": total,
                             "activeUsers": int(total * rng.uniform(0.55, 0.85)),
                             "userEngagementDuration": total * rng.uniform(20, 90)})
        for ch, src, med, share in traffic_mix:
            v = int(total * share * rng.uniform(0.5, 1.5))
            if v:
                out["traffic"].append({**hp, "sessionDefaultChannelGroup": ch,
                                       "sessionSource": src, "sessionMedium": med, "screenPageViews": v})
        for c, share in countries:
            v = int(total * share * rng.uniform(0.5, 1.5))
            if v:
                out["geo"].append({**hp, "country": c, "screenPageViews": v})
        if rng.random() < 0.3:
            out["downloads"].append({**hp, "eventCount": int(total * rng.uniform(0.02, 0.15)) + 1})
    for m in month_keys:
        views = sum(r["screenPageViews"] for r in out["monthly"] if r["yearMonth"] == m)
        out["site_monthly"].append({"yearMonth": m, "screenPageViews": views, "activeUsers": int(views * 0.6)})
    return out
