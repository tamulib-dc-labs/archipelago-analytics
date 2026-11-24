"""Collection membership from Archipelago's public IIIF metadata endpoints.

Two endpoints are used (both are outside the site's Cloudflare challenge):

* ``/do/{uuid}/metadata/iiifmanifest3collection/default.jsonld``
  IIIF Presentation 3 Collection listing every member of a collection.
* ``/do/{uuid}/metadata/iiifmanifest/default.jsonld``
  An item's manifest; its ``partOf`` names the collection(s) it belongs to.

Item manifests are cached on disk so each is fetched only once.
"""

from __future__ import annotations

import html
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import requests

UUID_RE = re.compile(
    r"/do/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", re.I
)
TAG_RE = re.compile(r"<[^>]+>")


def uuid_from(value: str) -> str | None:
    m = UUID_RE.search(value or "")
    return m.group(1).lower() if m else None


def clean_label(label) -> str:
    """IIIF language map -> plain text (labels here are often double-escaped)."""
    text = ""
    if isinstance(label, dict):
        for values in label.values():
            if values:
                text = values[0]
                break
    elif isinstance(label, list):
        text = label[0] if label else ""
    elif isinstance(label, str):
        text = label
    for _ in range(3):
        unescaped = html.unescape(text)
        if unescaped == text:
            break
        text = unescaped
    return TAG_RE.sub("", text).strip()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


RESTRICTED_RECHECK_DAYS = 30


class Restricted(Exception):
    """Drupal answered 403: the object is unpublished or access-controlled."""


def _is_cloudflare(r: requests.Response) -> bool:
    return bool(r.headers.get("cf-mitigated")) or "<title>Just a moment" in r.text[:2000]


class Archipelago:
    def __init__(self, cfg: dict, cache_dir: Path):
        self.base = cfg["base_url"].rstrip("/")
        self.delay = float(cfg.get("request_delay_seconds", 0.2))
        self.workers = int(cfg.get("request_workers", 4))
        self.session = requests.Session()
        self.session.headers["User-Agent"] = cfg.get("user_agent", "archipelago-analytics")
        self.session.headers["Accept"] = "application/ld+json, application/json"
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.items_path = cache_dir / "items.json"
        self.collections_path = cache_dir / "collections.json"
        self.items: dict = self._load(self.items_path)
        self.collections: dict = self._load(self.collections_path)
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ cache
    @staticmethod
    def _load(path: Path) -> dict:
        return json.loads(path.read_text()) if path.exists() else {}

    def save(self) -> None:
        for path, data in ((self.items_path, self.items), (self.collections_path, self.collections)):
            path.write_text(json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False))

    # ------------------------------------------------------------------- http
    def _get_json(self, url: str):
        for attempt in range(3):
            time.sleep(self.delay)
            try:
                r = self.session.get(url, timeout=60)
            except requests.RequestException:
                time.sleep(2 ** attempt)
                continue
            if r.status_code == 404:
                return None
            ctype = r.headers.get("content-type", "")
            if r.ok and "json" in ctype:
                return r.json()
            if r.status_code == 403 and not _is_cloudflare(r):
                raise Restricted(url)  # Drupal access denied: permanent, don't retry
            if r.status_code in (403, 429, 503):  # Cloudflare challenge / rate limit
                time.sleep(5 * (attempt + 1))
                continue
            return None
        raise RuntimeError(f"Could not fetch {url} (blocked or rate limited)")

    # ------------------------------------------------------------ item lookups
    def fetch_item(self, uuid: str) -> dict:
        """Fetch one object's manifest: label, ADO type, and `partOf` parents.

        `partOf` mixes two relations: membership in a collection (ismemberof)
        and being a part of a compound object (ispartof, e.g. a page of a
        Book). The parent's `ado_type` tells them apart.
        """
        url = f"{self.base}/do/{uuid}/metadata/iiifmanifest/default.jsonld"
        try:
            data = self._get_json(url)
        except Restricted:
            data, restricted = None, True
        except RuntimeError:
            return {}  # leave uncached; retry next run
        else:
            restricted = False
        if data is None:
            entry = {"label": None, "parents": [], "ado_type": None, "fetched": _now(),
                     **({"restricted": True} if restricted else {"missing": True})}
        else:
            parents = sorted({u for p in data.get("partOf", []) if (u := uuid_from(p.get("id", "")))})
            ado_type = None
            for m in data.get("metadata", []):
                if clean_label(m.get("label")) == "ADO Type":
                    ado_type = clean_label(m.get("value")) or None
                    break
            entry = {
                "label": clean_label(data.get("label")),
                "ado_type": ado_type,
                "parents": parents,
                "fetched": _now(),
            }
        with self._lock:
            self.items[uuid] = entry
        return entry

    def fetch_ancestors(self, uuids) -> None:
        """Make sure every ancestor of `uuids` is fetched (type + its own parents)."""
        known: set[str] = set()
        frontier = {p for u in uuids for p in self.items.get(u, {}).get("parents", [])}
        while frontier:
            todo = [u for u in frontier if "ado_type" not in self.items.get(u, {}) or not self.items[u].get("fetched")]
            if todo:
                print(f"  fetching {len(todo)} parent objects to tell collections from compound objects…")
                with ThreadPoolExecutor(self.workers) as pool:
                    list(pool.map(self.fetch_item, todo))
            known |= frontier
            frontier = {p for u in frontier for p in self.items.get(u, {}).get("parents", [])} - known
        self.save()

    @staticmethod
    def _needs_fetch(entry: dict) -> bool:
        if not entry.get("fetched"):
            return True
        if entry.get("restricted"):  # may have been published since
            age = datetime.now(timezone.utc) - datetime.fromisoformat(entry["fetched"])
            return age.days >= RESTRICTED_RECHECK_DAYS
        return False

    def fetch_items(self, uuids, limit: int) -> int:
        # Entries seeded from a collection listing have no `fetched` yet: their
        # own manifest may name further parents, so fetch them too.
        todo = [u for u in uuids if self._needs_fetch(self.items.get(u, {}))][:limit]
        if not todo:
            return 0
        print(f"  fetching {len(todo)} item manifests ({self.workers} workers)…")
        done = 0
        with ThreadPoolExecutor(self.workers) as pool:
            for _ in pool.map(self.fetch_item, todo):
                done += 1
                if done % 250 == 0:
                    print(f"    {done}/{len(todo)}")
                    self.save()
        self.save()
        return len(todo)

    # ------------------------------------------------------ collection lookups
    def fetch_collection(self, uuid: str) -> dict | None:
        """Refresh a collection's member list; fall back to the cached copy on failure."""
        url = f"{self.base}/do/{uuid}/metadata/iiifmanifest3collection/default.jsonld"
        try:
            data = self._get_json(url)
        except RuntimeError as exc:
            print(f"  ! {exc}; using cached membership")
            return self.collections.get(uuid)
        if not data or data.get("type") != "Collection":
            return self.collections.get(uuid)
        members, subcollections = [], []
        for entry in data.get("items", []):
            child = uuid_from(entry.get("id", ""))
            if not child:
                continue
            if entry.get("type") == "Collection":
                subcollections.append(child)
            else:
                members.append(child)
                # Labels come free with the listing; seed the item cache.
                self.items.setdefault(child, {"label": clean_label(entry.get("label")), "parents": [], "fetched": None})
        coll = {
            "label": clean_label(data.get("label")),
            "members": sorted(set(members)),
            "subcollections": sorted(set(subcollections)),
            "fetched": _now(),
        }
        self.collections[uuid] = coll
        return coll

    def resolve_collections(self, seeds) -> dict:
        """Fetch seeds and any nested sub-collections, breadth first."""
        queue, seen = list(dict.fromkeys(seeds)), set()
        while queue:
            uuid = queue.pop(0)
            if uuid in seen:
                continue
            seen.add(uuid)
            coll = self.fetch_collection(uuid)
            if coll:
                print(f"  collection {uuid}: {coll['label']!r} – {len(coll['members'])} items")
                queue.extend(coll.get("subcollections", []))
        self.save()
        return {u: self.collections[u] for u in seen if u in self.collections}
