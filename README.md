# Archipelago Analytics

A static GitHub Pages dashboard showing Google Analytics 4 usage for
[TAMU Digital Collections](https://digitalcollections.library.tamu.edu/) (Archipelago).
It rolls up traffic by **collection** and shows where visitors come from.

## How it works

```
GA4 Data API ──► every /do/{uuid} page: views, users, engagement, downloads,
                 channel / source / medium, country, month
Archipelago   ──► /do/{item}/metadata/iiifmanifest/default.jsonld        → partOf (which collections)
                  /do/{coll}/metadata/iiifmanifest3collection/default.jsonld → member list
                         │
                 analytics/build.py joins them locally
                         ▼
                 site/data/index.json + site/data/collections/{uuid}.json
                         ▼
                 site/ (plain HTML + JS, no build step) → GitHub Pages
```

* **No per-item GA queries.** Each GA report pulls *all* `/do/` pages at once
  (six paginated requests in total), and the join happens in Python.
* **Collections are discovered automatically.** Every item with traffic has its
  manifest's `partOf` read once, and the result is cached in `data/cache/`. That
  finds every collection with traffic. Use `config.yml → collections` to add any
  collection that should always appear, even with no traffic yet.
* **Nested collections** are followed, and parent totals include their sub-collections.
* The public HTML pages, `/jsonapi`, and the sitemap are behind a Cloudflare
  challenge. These IIIF metadata endpoints are not, which is why they're used.

## Beyond /do/ pages

* **`landing_pages.yml`**: maps pretty collection URLs (such as `/yearbooks`)
  to collection UUIDs. Their views count toward that collection as landing-page
  views. The file was pre-filled by matching GA page titles to collection names.
* **`page_groups` in `config.yml`**: sections reported on their own, such as the
  *Aggieland Through Time* exhibit (`detail: full`, with sources, countries, and
  per-page tables) and *Search* (`detail: basic`, with views and users only).
  Each group costs one extra GA request per run. Basic groups share one more
  request between them.
* **`hostnames`**: limits every report to the production sites, which drops
  `localhost` and GitHub Pages development traffic.
* **Unpublished or restricted objects** return Drupal's 403 page. They're cached
  as `restricted` and re-checked every 30 days, so they don't slow down each run.

## Run locally

```bash
uv run python -m analytics.build --sample   # real membership, generated traffic
python3 -m http.server 8765 --directory site
```

With real GA data, authenticate first. Use either
`gcloud auth application-default login --scopes=https://www.googleapis.com/auth/analytics.readonly,https://www.googleapis.com/auth/cloud-platform`
or `export GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json`. Then run:

```bash
uv run python -m analytics.build
```

The first real run looks up one manifest per viewed item. This happens at about
5 requests per second and is capped by `max_new_manifest_fetches`. Later runs
only look up new items.

## GitHub setup

1. **Google Cloud:** create a project, enable the **Google Analytics Data API**,
   and create a service account.
2. **GA4:** Admin → Property access management → add the service account's
   email as a **Viewer** on property `555926229`.
3. **Credentials:** pick one.
   * Simple: create a JSON key and save it as the repository secret `GA_CREDENTIALS_JSON`.
   * Keyless: set up Workload Identity Federation for this repository, then set
     the repository variables `GCP_WORKLOAD_IDENTITY_PROVIDER` and `GCP_SERVICE_ACCOUNT`.
4. **Pages:** Settings → Pages → Source: **GitHub Actions**.
5. **First run:** Actions → *Harvest analytics and deploy site* → *Run workflow*.
   After that it runs daily.

## Reading the numbers

* **Views** are GA4 `screenPageViews` on `/do/{uuid}` pages and add up across items.
* **Users** are per item. One person who views five items counts five times if
  you add them up, so the site never sums them. Site-wide users by month come
  from a separate GA query, where GA removes the duplicates itself.
* **Downloads** are GA4 enhanced-measurement `file_download` events, credited
  to the page they happened on.
* **Not counted:** IIIF viewer activity (zooming and paging inside Mirador/UV)
  and direct image or IIIF API hits. GA only sees page loads and events.
* GA4 may hide low-count rows (thresholding) when Google Signals is on.

## Ideas for later

* Send the collection UUID as GA4's `content_group` from the Archipelago Twig
  template. GA would then group by collection natively, including accurate
  de-duplicated users per collection.
* Track viewer interactions (page turns, zoom) as GA events.
