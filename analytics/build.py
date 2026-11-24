"""Harvest GA4 + Archipelago membership and write JSON for the static site.

    uv run python -m analytics.build            # real GA data
    uv run python -m analytics.build --sample   # fake GA data, real membership
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import yaml

from .archipelago import Archipelago, uuid_from
from .ga import run_reports, sample_reports

ROOT = Path(__file__).resolve().parent.parent
TOP_SOURCES = 25


def month_label(ym: str) -> str:
    return f"{ym[:4]}-{ym[4:]}"


UUID_KEY = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def is_uuid(key: str) -> bool:
    return bool(UUID_KEY.match(key))


def norm_path(path: str) -> str:
    """/a/b/ , /a/b/index.html and /a/b are the same page."""
    path = path.split("?")[0]
    if path.endswith("/index.html"):
        path = path[: -len("index.html")]
    return path.rstrip("/") or "/"


def page_key(host: str, path: str, prefix: str) -> str:
    """Digital objects are keyed by UUID; every other page by host + normalized path."""
    if path.startswith(prefix) and (u := uuid_from(path)):
        return u
    return f"{host}{norm_path(path)}"


def under(path: str, roots: list[str]) -> str | None:
    """The root in `roots` that `path` equals or sits below, if any."""
    for root in roots:
        r = norm_path(root)
        if path == r or path.startswith(r.rstrip("/") + "/"):
            return r
    return None


class Index:
    """GA rows grouped by page key (digital-object UUID, or host+path)."""

    def __init__(self, reports: dict[str, list[dict]], prefix: str):
        self.by_uuid: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
        for name, rows in reports.items():
            for row in rows:
                if "pagePath" in row:
                    self.by_uuid[page_key(row.get("hostName", ""), row["pagePath"], prefix)][name].append(row)

    def uuids(self) -> set[str]:
        """Digital objects with traffic."""
        return {k for k in self.by_uuid if is_uuid(k)}

    def keys(self) -> set[str]:
        return set(self.by_uuid)

    def total(self, uuid: str, report: str, metric: str) -> float:
        return sum(r[metric] for r in self.by_uuid.get(uuid, {}).get(report, []))


def summarize(objects: dict[str, set[str]], idx: Index, items_meta: dict, labels: dict[str, str], base: str,
              item_limit: int | None, capped: bool = False, landing: set[str] = frozenset()) -> dict:
    """Aggregate GA rows. `objects` maps each top-level object (an item, or a
    compound object such as a Book) to the UUIDs whose traffic rolls up into
    it (the object itself plus its pages/parts). `landing` pages count in the
    totals and charts but are not listed as items."""
    all_uuids: set[str] = (set().union(*objects.values()) if objects else set()) | set(landing)
    monthly, channels, sources, countries = defaultdict(float), defaultdict(float), defaultdict(float), defaultdict(float)
    for u in all_uuids:
        rows = idx.by_uuid.get(u, {})
        for r in rows.get("monthly", []):
            monthly[r["yearMonth"]] += r["screenPageViews"]
        for r in rows.get("traffic", []):
            channels[r["sessionDefaultChannelGroup"]] += r["screenPageViews"]
            sources[(r["sessionSource"], r["sessionMedium"])] += r["screenPageViews"]
        for r in rows.get("geo", []):
            countries[r["country"]] += r["screenPageViews"]

    def tot(uuids, report, metric):
        return sum(idx.total(u, report, metric) for u in uuids)

    rows = []
    for obj, members in objects.items():
        views = tot(members, "items", "screenPageViews")
        rows.append({
            "uuid": obj,
            "label": labels.get(obj) or obj,
            "type": items_meta.get(obj, {}).get("ado_type") or "",
            "url": f"{base}/do/{obj}" if is_uuid(obj) else f"https://{obj}",
            "views": int(views),
            "users": int(tot(members, "items", "activeUsers")),
            "avg_engagement_s": round(tot(members, "items", "userEngagementDuration") / views, 1) if views else 0,
            "downloads": int(tot(members, "downloads", "eventCount")),
            "parts_viewed": sum(1 for u in members if u != obj and idx.total(u, "items", "screenPageViews")),
        })
    rows.sort(key=lambda i: (-i["views"], i["label"].lower()))
    total_views = tot(all_uuids, "items", "screenPageViews")
    return {
        "items_total": len(objects),
        "items_total_capped": capped,
        "items_viewed": sum(1 for i in rows if i["views"]),
        "views": int(total_views),
        "downloads": int(tot(all_uuids, "downloads", "eventCount")),
        "avg_engagement_s": round(tot(all_uuids, "items", "userEngagementDuration") / total_views, 1) if total_views else 0,
        "monthly": [{"month": month_label(m), "views": int(v)} for m, v in sorted(monthly.items())],
        "channels": [{"channel": k, "views": int(v)} for k, v in sorted(channels.items(), key=lambda kv: -kv[1])],
        "sources": [{"source": s, "medium": m, "views": int(v)}
                    for (s, m), v in sorted(sources.items(), key=lambda kv: -kv[1])[:TOP_SOURCES]],
        "countries": [{"country": k, "views": int(v)} for k, v in sorted(countries.items(), key=lambda kv: -kv[1])],
        "items": rows if item_limit is None else rows[:item_limit],
        "landing_views": int(tot(landing, "items", "screenPageViews")),
    }


def load_landing_pages(path: Path, base: str) -> dict[str, str]:
    """landing_pages.yml (path or URL → collection UUID) as page key → UUID."""
    if not path.exists():
        return {}
    host = urlparse(base).netloc
    out = {}
    for page, uuid in (yaml.safe_load(path.read_text()) or {}).items():
        page = str(page)
        u = urlparse(page if "://" in page else f"https://{host}/{page.lstrip('/')}")
        out[f"{u.netloc}{norm_path(u.path)}"] = str(uuid).strip().lower()
    return out


def page_label(rel: str, home: str) -> str:
    """works/administration-building.html → Administration Building."""
    seg = rel.strip("/").split("/")[-1] if rel.strip("/") else ""
    if not seg:
        return home
    seg = re.sub(r"\.html?$", "", seg).replace("-", " ").replace("_", " ")
    return " ".join(w[:1].upper() + w[1:] for w in seg.split())


LISTING_CAP = 1000  # the iiifmanifest3collection display stops at 1000 members


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=ROOT / "config.yml", type=Path)
    ap.add_argument("--out", default=ROOT / "site" / "data", type=Path)
    ap.add_argument("--sample", action="store_true", help="use generated GA data (no credentials needed)")
    ap.add_argument("--no-discover", action="store_true", help="only use collections listed in config")
    args = ap.parse_args()

    cfg = yaml.safe_load(args.config.read_text())
    base = cfg["base_url"].rstrip("/")
    prefix = cfg.get("path_prefix", "/do/")
    arch = Archipelago(cfg, ROOT / "data" / "cache")
    seeds = {s.lower() for s in cfg.get("collections") or []}
    hostnames = cfg.get("hostnames") or []
    landing_of = load_landing_pages(ROOT / cfg.get("landing_pages_file", "landing_pages.yml"), base)
    groups = cfg.get("page_groups") or []
    # Pages that get full per-page reports: digital objects, pretty collection URLs, full-detail groups.
    detail_paths = [prefix] + ["/" + k.split("/", 1)[1] for k in landing_of if "/" in k] \
        + [p for g in groups if g.get("detail") == "full" for p in g["paths"]]

    print("1. GA4 reports")
    if args.sample:
        seeded = arch.resolve_collections(sorted(seeds))
        reports = sample_reports(sorted({m for c in seeded.values() for m in c["members"]}), prefix, urlparse(base).netloc)
    else:
        reports = run_reports(
            str(cfg["ga_property_id"]), str(cfg["start_date"]), str(cfg.get("end_date", "yesterday")),
            hostnames, detail_paths, prefix,
            {g["id"]: (g.get("host"), g["paths"]) for g in groups},
            [p for g in groups if g.get("detail") != "full" for p in g["paths"]],
        )
    idx = Index(reports, prefix)
    seen = idx.uuids()
    print(f"   {len(seen)} digital objects with traffic")

    print("2. Collection membership")
    discover = cfg.get("discover_collections", True) and not args.no_discover
    if discover:
        busiest = sorted(seen, key=lambda u: -idx.total(u, "items", "screenPageViews"))
        arch.fetch_items(busiest, int(cfg.get("max_new_manifest_fetches", 3000)))
    arch.fetch_ancestors(seen)
    meta_items = arch.items

    def is_collection(u: str) -> bool:
        return u in seeds or meta_items.get(u, {}).get("ado_type") == "Collection"

    memo: dict[str, set[tuple[str, str]]] = {}

    def placements(u: str, stack: tuple = ()) -> set[tuple[str, str]]:
        """(collection, top-level object) pairs reached by walking up partOf.

        A page's parent is usually a compound object (Book, Album, …) whose
        parent is the collection; the compound object is the top-level object.
        """
        if u in memo:
            return memo[u]
        out: set[tuple[str, str]] = set()
        if len(stack) < 12:
            for p in meta_items.get(u, {}).get("parents", []):
                if p in stack:
                    continue
                if is_collection(p):
                    out.add((p, u))
                else:
                    out |= placements(p, stack + (u,))
        memo[u] = out
        return out

    found = {c for u in seen for c, _ in placements(u)} | {u for u in seen if is_collection(u)}
    wanted = (seeds | found | set(landing_of.values())) if discover else (seeds | set(landing_of.values()))
    collections = arch.resolve_collections(sorted(wanted))

    # collection -> top-level object -> uuids rolling into it
    direct: dict[str, dict[str, set[str]]] = {c: defaultdict(set) for c in collections}
    for c, info in collections.items():
        for m in info["members"]:
            if not is_collection(m):
                direct[c][m].add(m)
    site_objects: dict[str, set[str]] = defaultdict(set)
    unassigned: set[str] = set()
    for u in seen:
        if is_collection(u) or u in collections:
            continue
        places = placements(u)
        for c, top in places:
            if c in direct:
                direct[c][top].add(u)
        tops = {top for c, top in places if c in direct}
        if not tops:
            unassigned.add(u)
            tops = {u}
        for top in tops:
            site_objects[top].add(u)

    def rolled(c: str, stack: tuple = ()) -> dict[str, set[str]]:
        out: dict[str, set[str]] = defaultdict(set)
        for obj, uu in direct.get(c, {}).items():
            out[obj] |= uu
        for sub in collections.get(c, {}).get("subcollections", []):
            if sub in collections and sub not in stack:
                for obj, uu in rolled(sub, stack + (c,)).items():
                    out[obj] |= uu
        return out

    labels = {u: i.get("label") for u, i in meta_items.items()}
    labels.update({c: info["label"] for c, info in collections.items()})
    parents = defaultdict(list)
    for c, info in collections.items():
        for sub in info.get("subcollections", []):
            parents[sub].append(c)

    print("3. Writing site data")
    out = args.out
    (out / "collections").mkdir(parents=True, exist_ok=True)
    for old in (out / "collections").glob("*.json"):
        old.unlink()
    generated = datetime.now(timezone.utc).isoformat(timespec="seconds")
    meta = {"generated": generated, "sample": args.sample, "base_url": base,
            "start_date": str(cfg["start_date"]), "end_date": str(cfg.get("end_date", "yesterday"))}

    coll_rows = []
    for c, info in collections.items():
        capped = len(info["members"]) >= LISTING_CAP
        landing = {c} | {k for k, v in landing_of.items() if v == c}
        s = summarize(rolled(c), idx, meta_items, labels, base, None, capped, landing)
        s.update(meta)
        s.update({
            "uuid": c, "label": info["label"], "url": f"{base}/do/{c}",
            "landing_pages": sorted(f"https://{k}" for k in landing if not is_uuid(k)),
            "subcollections": [{"uuid": x, "label": labels.get(x) or x} for x in info.get("subcollections", [])],
            "parents": [{"uuid": x, "label": labels.get(x) or x} for x in parents.get(c, [])],
        })
        (out / "collections" / f"{c}.json").write_text(json.dumps(s, ensure_ascii=False))
        coll_rows.append({k: s[k] for k in ("uuid", "label", "items_total", "items_total_capped", "items_viewed",
                                            "views", "downloads", "avg_engagement_s", "landing_views")}
                         | {"parents": [p["uuid"] for p in s["parents"]]})
    coll_rows.sort(key=lambda r: -r["views"])

    site = summarize(site_objects, idx, meta_items, labels, base, 100)
    site["monthly"] = [{"month": month_label(r["yearMonth"]), "views": int(r["screenPageViews"]),
                        "users": int(r["activeUsers"])} for r in sorted(reports.get("site_monthly", []), key=lambda r: r["yearMonth"])]
    # ---------------------------------------------------------- page groups
    (out / "groups").mkdir(parents=True, exist_ok=True)
    for old in (out / "groups").glob("*.json"):
        old.unlink()
    group_rows = []
    for g in groups:
        gid, host = g["id"], g.get("host") or urlparse(base).netloc
        monthly = sorted(reports.get(f"group:{gid}", []), key=lambda r: r["yearMonth"])
        total = (reports.get(f"group:{gid}:total") or [{}])[0]
        views = total.get("screenPageViews", sum(r["screenPageViews"] for r in monthly))
        head = {
            "id": gid, "label": g["label"], "kind": g.get("kind", ""), "detail": g.get("detail", "basic"),
            "url": f"https://{host}{norm_path(g['paths'][0])}",
            "views": int(views), "users": int(total.get("activeUsers", 0)),
            "avg_engagement_s": round(total.get("userEngagementDuration", 0) / views, 1) if views else 0,
        }
        if head["detail"] == "full":
            keys = [k for k in idx.keys() if k.startswith(host + "/") and under(k[len(host):], g["paths"])]
            rel = {k: k[len(host) + len(under(k[len(host):], g["paths"])):] for k in keys}
            glabels = {k: page_label(rel[k], f"{g['label']} home") for k in keys}
            gmeta = {k: {"ado_type": rel[k].lstrip("/") or "/"} for k in keys}
            body = summarize({k: {k} for k in keys}, idx, gmeta, glabels, base, None)
            body.pop("landing_views")
        else:
            pages = defaultdict(lambda: [0.0, 0.0])
            for r in reports.get("basic_pages", []):
                if r["hostName"] == host and under(norm_path(r["pagePath"]), g["paths"]):
                    pp = pages[norm_path(r["pagePath"])]
                    pp[0] += r["screenPageViews"]; pp[1] += r["activeUsers"]
            body = {"pages": [{"path": k, "url": f"https://{host}{k}", "views": int(v), "users": int(u)}
                              for k, (v, u) in sorted(pages.items(), key=lambda kv: -kv[1][0])]}
        body.update(head)
        body.update(meta)
        body["monthly"] = [{"month": month_label(r["yearMonth"]), "views": int(r["screenPageViews"]),
                            "users": int(r["activeUsers"])} for r in monthly]
        (out / "groups" / f"{gid}.json").write_text(json.dumps(body, ensure_ascii=False))
        group_rows.append(head)

    index = meta | {
        "groups": group_rows,
        "site": site,
        "collections": coll_rows,
        "unassigned": {"items_viewed": len(unassigned),
                       "views": int(sum(idx.total(u, "items", "screenPageViews") for u in unassigned)),
                       "unresolved": sum(1 for u in unassigned if not meta_items.get(u, {}).get("fetched")),
                       "restricted": sum(1 for u in unassigned if meta_items.get(u, {}).get("restricted")),
                       "restricted_views": int(sum(idx.total(u, "items", "screenPageViews") for u in unassigned
                                                   if meta_items.get(u, {}).get("restricted")))},
    }
    (out / "index.json").write_text(json.dumps(index, ensure_ascii=False))
    arch.save()
    print(f"   {len(coll_rows)} collections, {site['views']:,} views across {site['items_viewed']:,} objects "
          f"({len(unassigned):,} viewed objects not yet placed in a collection) → {out}")


if __name__ == "__main__":
    main()
