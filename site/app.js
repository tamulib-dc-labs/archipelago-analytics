/* Digital Collections Analytics — static dashboard over site/data/*.json */
(() => {
  "use strict";

  const main = document.getElementById("main");
  const cache = new Map();
  let redraws = [];

  // ------------------------------------------------------------- formatting
  const nf = new Intl.NumberFormat("en-US");
  const cf = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });
  const fmt = (n) => nf.format(Math.round(n || 0));
  const pct = (a, b) => (b ? `${((a / b) * 100).toFixed(a / b < 0.1 ? 1 : 0)}%` : "–");
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const monthName = (ym, short) => {
    const [y, m] = ym.split("-");
    return short ? `${MONTHS[+m - 1]} ’${y.slice(2)}` : `${MONTHS[+m - 1]} ${y}`;
  };
  const dur = (s) => {
    s = Math.round(s || 0);
    if (!s) return "–";
    const m = Math.floor(s / 60);
    return m ? `${m}m ${String(s % 60).padStart(2, "0")}s` : `${s}s`;
  };
  const esc = (s) =>
    String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

  async function load(path) {
    if (!cache.has(path)) {
      cache.set(path, fetch(path, { cache: "no-cache" }).then((r) => {
        if (!r.ok) throw new Error(`${path}: ${r.status}`);
        return r.json();
      }));
    }
    return cache.get(path);
  }

  // ------------------------------------------------------------- components
  function tiles(list) {
    return `<div class="tiles">${list
      .map((t) => `<div class="tile"><div class="label">${esc(t.label)}</div><div class="value">${t.value}</div>${t.sub ? `<div class="sub">${t.sub}</div>` : ""}</div>`)
      .join("")}</div>`;
  }

  function niceStep(v) {
    const mag = 10 ** Math.floor(Math.log10(v));
    for (const f of [1, 2, 2.5, 5, 10]) if (f * mag >= v) return f * mag;
    return 10 * mag;
  }

  function fillMonths(points) {
    if (points.length < 2) return points;
    const byMonth = new Map(points.map((p) => [p.month, p]));
    const out = [];
    let [y, m] = points[0].month.split("-").map(Number);
    const last = points[points.length - 1].month;
    for (;;) {
      const key = `${y}-${String(m).padStart(2, "0")}`;
      out.push(byMonth.get(key) || { month: key, views: 0 });
      if (key >= last) break;
      if (++m > 12) { m = 1; y++; }
    }
    return out;
  }

  /** Single-series monthly line with crosshair tooltip. */
  function lineChart(el, points, extra = () => "") {
    const draw = () => {
      el.innerHTML = "";
      if (!points.length) { el.innerHTML = `<p class="empty">No views in this period.</p>`; return; }
      const W = Math.max(el.clientWidth, 280), H = 240;
      const m = { t: 12, r: 16, b: 28, l: 48 };
      const iw = W - m.l - m.r, ih = H - m.t - m.b, n = points.length;
      const peak = Math.max(...points.map((p) => p.views), 1);
      const step = niceStep(peak / 4), max = step * Math.ceil(peak / step);
      const x = (i) => m.l + (n === 1 ? iw / 2 : (i * iw) / (n - 1));
      const y = (v) => m.t + ih - (v / max) * ih;
      const every = Math.max(1, Math.ceil(n / Math.floor(iw / 64)));
      let s = `<svg viewBox="0 0 ${W} ${H}" height="${H}" role="img" aria-label="Views per month, ${esc(monthName(points[0].month))} to ${esc(monthName(points[n - 1].month))}, peak ${fmt(peak)}">`;
      for (let v = 0; v <= max + 1e-9; v += step) {
        s += `<line class="${v ? "gridline" : "baseline"}" x1="${m.l}" x2="${W - m.r}" y1="${y(v)}" y2="${y(v)}"/>`;
        s += `<text x="${m.l - 8}" y="${y(v) + 4}" text-anchor="end">${cf.format(v)}</text>`;
      }
      points.forEach((p, i) => {
        if ((n - 1 - i) % every === 0) s += `<text x="${x(i)}" y="${H - 8}" text-anchor="middle">${monthName(p.month, true)}</text>`;
      });
      const line = points.map((p, i) => `${i ? "L" : "M"}${x(i)},${y(p.views)}`).join("");
      s += `<path class="area" d="${line}L${x(n - 1)},${y(0)}L${x(0)},${y(0)}Z"/>`;
      s += `<path class="line" d="${line}"/>`;
      s += `<line class="crosshair" y1="${m.t}" y2="${m.t + ih}" visibility="hidden"/><circle class="dot" r="4.5" visibility="hidden"/>`;
      s += `<rect x="${m.l - 10}" y="0" width="${iw + 20}" height="${H}" fill="transparent"/></svg>`;
      el.innerHTML = s + `<div class="tooltip" hidden></div>`;
      const svg = el.querySelector("svg"), tip = el.querySelector(".tooltip");
      const ch = svg.querySelector(".crosshair"), dot = svg.querySelector(".dot");
      const show = (i) => {
        const p = points[i];
        ch.setAttribute("x1", x(i)); ch.setAttribute("x2", x(i)); ch.setAttribute("visibility", "visible");
        dot.setAttribute("cx", x(i)); dot.setAttribute("cy", y(p.views)); dot.setAttribute("visibility", "visible");
        tip.hidden = false;
        tip.innerHTML = `<span class="muted">${monthName(p.month)}</span><br><b>${fmt(p.views)}</b> views${extra(p)}`;
        tip.style.left = `${Math.min(Math.max(x(i), 70), W - 70)}px`;
        tip.style.top = `${y(p.views) - 10}px`;
      };
      svg.addEventListener("pointermove", (e) => {
        const r = svg.getBoundingClientRect();
        const px = ((e.clientX - r.left) / r.width) * W;
        show(Math.max(0, Math.min(n - 1, Math.round(((px - m.l) / iw) * (n - 1)))));
      });
      svg.addEventListener("pointerleave", () => {
        tip.hidden = true; ch.setAttribute("visibility", "hidden"); dot.setAttribute("visibility", "hidden");
      });
    };
    draw();
    redraws.push(draw);
  }

  /** Horizontal bars, values labelled; long tails fold into "Other". */
  function barList(rows, nameKey, limit = 10) {
    if (!rows.length) return `<p class="empty">No data.</p>`;
    const total = rows.reduce((a, r) => a + r.views, 0);
    let shown = rows.slice(0, limit);
    if (rows.length > limit) {
      const rest = rows.slice(limit);
      shown = shown.concat({ [nameKey]: `Other (${rest.length})`, views: rest.reduce((a, r) => a + r.views, 0) });
    }
    const max = Math.max(...shown.map((r) => r.views), 1);
    return `<ul class="bars">${shown
      .map((r) => `<li title="${esc(r[nameKey])}: ${fmt(r.views)} views (${pct(r.views, total)})"><span class="name">${esc(r[nameKey])}</span><span class="bar"><span style="width:${(r.views / max) * 100}%"></span></span><span class="val">${fmt(r.views)}<small>${pct(r.views, total)}</small></span></li>`)
      .join("")}</ul>`;
  }

  /** Sortable, searchable table with CSV export. */
  function dataTable(el, { columns, rows, sort, searchKey, zeroToggle, pageSize = 50, csvName, label }) {
    const state = { key: sort, dir: -1, q: "", hideZero: false, limit: pageSize };
    const tools = [];
    if (searchKey) tools.push(`<input type="search" placeholder="Filter by title…" aria-label="Filter ${esc(label)}">`);
    if (zeroToggle) tools.push(`<label><input type="checkbox" class="hidezero"> Hide items with no views</label>`);
    tools.push(`<span class="count" aria-live="polite"></span>`);
    if (csvName) tools.push(`<button type="button" class="csv">Download CSV</button>`);
    el.innerHTML = `<div class="table-tools">${tools.join("")}</div><div class="scroll"><table><thead><tr>${columns
      .map((c) => `<th class="${c.num ? "num" : ""}" data-key="${c.key}" ${c.sortable === false ? "" : 'aria-sort="none"'}>${c.sortable === false ? esc(c.label) : `<button class="sort" type="button">${esc(c.label)}</button>`}</th>`)
      .join("")}</tr></thead><tbody></tbody></table></div><p class="more"></p>`;
    const tbody = el.querySelector("tbody"), count = el.querySelector(".count"), more = el.querySelector(".more");
    const max = Math.max(...rows.map((r) => r.views || 0), 1);

    const filtered = () => {
      const q = state.q.toLowerCase();
      return rows
        .filter((r) => (!q || String(r[searchKey]).toLowerCase().includes(q)) && (!state.hideZero || r.views))
        .sort((a, b) => {
          const av = a[state.key], bv = b[state.key];
          return (typeof av === "string" ? av.localeCompare(bv) : (av || 0) - (bv || 0)) * state.dir;
        });
    };
    const render = () => {
      const list = filtered();
      tbody.innerHTML = list.slice(0, state.limit)
        .map((r) => `<tr class="${r.views ? "" : "zero"}">${columns.map((c) => `<td class="${c.num ? "num" : ""} ${c.cls || ""}">${c.render ? c.render(r, max) : esc(r[c.key])}</td>`).join("")}</tr>`)
        .join("") || `<tr><td colspan="${columns.length}" class="empty">Nothing matches.</td></tr>`;
      count.textContent = `${fmt(Math.min(list.length, state.limit))} of ${fmt(list.length)} shown`;
      more.innerHTML = list.length > state.limit ? `<button type="button">Show ${fmt(Math.min(pageSize * 4, list.length - state.limit))} more</button>` : "";
      el.querySelectorAll("th[aria-sort]").forEach((th) => {
        th.setAttribute("aria-sort", th.dataset.key === state.key ? (state.dir < 0 ? "descending" : "ascending") : "none");
      });
    };
    el.querySelectorAll("th button.sort").forEach((b) => b.addEventListener("click", () => {
      const key = b.parentElement.dataset.key;
      state.dir = state.key === key ? -state.dir : (typeof rows[0]?.[key] === "string" ? 1 : -1);
      state.key = key;
      render();
    }));
    el.querySelector("input[type=search]")?.addEventListener("input", (e) => { state.q = e.target.value; state.limit = pageSize; render(); });
    el.querySelector(".hidezero")?.addEventListener("change", (e) => { state.hideZero = e.target.checked; render(); });
    more.addEventListener("click", (e) => { if (e.target.closest("button")) { state.limit += pageSize * 4; render(); } });
    el.querySelector(".csv")?.addEventListener("click", () => {
      const keys = columns.map((c) => c.csv || c.key).flat();
      const lines = [keys.join(",")].concat(filtered().map((r) => keys.map((k) => `"${String(r[k] ?? "").replace(/"/g, '""')}"`).join(",")));
      const a = Object.assign(document.createElement("a"), {
        href: URL.createObjectURL(new Blob([lines.join("\n")], { type: "text/csv" })),
        download: csvName,
      });
      a.click();
      URL.revokeObjectURL(a.href);
    });
    render();
  }

  const viewsCell = (r, max) =>
    `<span class="inline-bar"><span>${fmt(r.views)}</span><span class="track"><i style="width:${(r.views / max) * 100}%"></i></span></span>`;

  const itemColumns = [
    {
      key: "label", label: "Item", csv: ["label", "type", "uuid", "url", "parts_viewed"],
      render: (r) => {
        const bits = [r.type, r.parts_viewed ? `${fmt(r.parts_viewed)} ${r.parts_viewed === 1 ? "page" : "pages"} viewed, included in totals` : ""].filter(Boolean);
        return `<a href="${esc(r.url)}">${esc(r.label)}</a>${bits.length ? `<br><small class="subtle">${esc(bits.join(" · "))}</small>` : ""}`;
      },
    },
    { key: "views", label: "Views", num: true, cls: "viewscell", render: viewsCell },
    { key: "users", label: "Users", num: true, render: (r) => fmt(r.users) },
    { key: "avg_engagement_s", label: "Avg. engagement / view", num: true, render: (r) => dur(r.avg_engagement_s) },
    { key: "downloads", label: "Downloads", num: true, render: (r) => fmt(r.downloads) },
  ];

  function sourceTable(sources) {
    if (!sources.length) return `<p class="empty">No data.</p>`;
    const total = sources.reduce((a, r) => a + r.views, 0);
    return `<div class="scroll"><table><thead><tr><th>Source</th><th>Medium</th><th class="num">Views</th><th class="num">Share of top ${sources.length}</th></tr></thead><tbody>${sources
      .map((s) => `<tr><td>${esc(s.source)}</td><td>${esc(s.medium)}</td><td class="num">${fmt(s.views)}</td><td class="num">${pct(s.views, total)}</td></tr>`)
      .join("")}</tbody></table></div>`;
  }

  function monthTable(points) {
    return `<details class="data-table"><summary>View as table</summary><div class="scroll"><table><thead><tr><th>Month</th><th class="num">Views</th>${points[0]?.users !== undefined ? '<th class="num">Users</th>' : ""}</tr></thead><tbody>${points
      .map((p) => `<tr><td>${monthName(p.month)}</td><td class="num">${fmt(p.views)}</td>${p.users !== undefined ? `<td class="num">${fmt(p.users)}</td>` : ""}</tr>`)
      .join("")}</tbody></table></div></details>`;
  }

  function trafficPanels(d) {
    return `<div class="grid-2">
      <section class="panel"><h3>How visitors arrive</h3>${barList(d.channels, "channel", 8)}
        <p class="note">GA4 default channel grouping of the session that viewed the page.</p></section>
      <section class="panel"><h3>Where visitors are</h3>${barList(d.countries, "country", 10)}</section>
    </div>
    <section class="panel"><h3>Top referring sources</h3>${sourceTable(d.sources)}</section>`;
  }

  function banner(d) {
    return d.sample
      ? `<div class="banner" role="note"><strong>Sample data.</strong> Collection membership is real, but the traffic numbers are generated. Run the harvester with GA credentials to see real usage.</div>`
      : "";
  }

  function rangeText(d) {
    return `${esc(d.start_date)} to ${esc(d.end_date)} · updated ${new Date(d.generated).toLocaleString()}`;
  }

  // ----------------------------------------------------------------- views
  async function overview() {
    const d = await load("data/index.json");
    const s = d.site;
    document.title = "Digital Collections Analytics · Texas A&M University Libraries";
    const un = d.unassigned;
    main.innerHTML = `${banner(d)}
      <h2 class="page-title">All digital objects</h2>
      <p class="meta">${rangeText(d)}</p>
      ${tiles([
        { label: "Page views", value: fmt(s.views) },
        { label: "Items viewed", value: fmt(s.items_viewed) },
        { label: "Collections", value: fmt(d.collections.length) },
        { label: "Downloads", value: fmt(s.downloads), sub: "file_download events" },
        { label: "Avg. engagement", value: dur(s.avg_engagement_s), sub: "per page view" },
      ])}
      <section class="panel"><h3>Views per month</h3><div class="chart" id="trend"></div>${monthTable(s.monthly)}</section>
      ${trafficPanels(s)}
      ${(d.groups || []).length ? `<section class="panel"><h3>Exhibits and site sections</h3><div class="scroll"><table><thead><tr><th>Section</th><th>Type</th><th class="num">Views</th><th class="num">Users</th><th class="num">Avg. engagement</th></tr></thead><tbody>${d.groups
        .map((g) => `<tr><td><a href="#/g/${esc(g.id)}">${esc(g.label)}</a></td><td>${esc(g.kind)}</td><td class="num">${fmt(g.views)}</td><td class="num">${fmt(g.users)}</td><td class="num">${dur(g.avg_engagement_s)}</td></tr>`)
        .join("")}</tbody></table></div><p class="note">Not included in the digital-object totals above. Users are de-duplicated within each section.</p></section>` : ""}
      <section class="panel"><h3>Collections</h3><div id="colls"></div>
        ${un.items_viewed ? `<p class="note">${fmt(un.items_viewed)} viewed items (${fmt(un.views)} views) are not in a known collection${un.restricted ? `. ${fmt(un.restricted)} of them (${fmt(un.restricted_views)} views) are unpublished or restricted, so their collection can’t be looked up; these are most likely staff views` : ""}${un.unresolved ? `; ${fmt(un.unresolved)} have not been looked up yet and will be on later runs` : ""}.</p>` : ""}
      </section>
      <section class="panel"><h3>Most viewed items</h3><div id="items"></div></section>`;
    lineChart(document.getElementById("trend"), s.monthly, (p) => (p.users !== undefined ? `<br><b>${fmt(p.users)}</b> users` : ""));
    dataTable(document.getElementById("colls"), {
      label: "collections",
      rows: d.collections.map((c) => ({ ...c, viewed_share: c.items_total ? c.items_viewed / c.items_total : 0 })),
      sort: "views", searchKey: "label", csvName: "collections.csv",
      columns: [
        { key: "label", label: "Collection", render: (r) => `<a href="#/c/${r.uuid}">${esc(r.label)}</a>`, csv: ["label", "uuid"] },
        { key: "items_total", label: "Items", num: true, render: (r) => `${r.items_total_capped ? "≥ " : ""}${fmt(r.items_total)}` },
        { key: "viewed_share", label: "Items viewed", num: true, render: (r) => `${fmt(r.items_viewed)} <small>(${pct(r.items_viewed, r.items_total)})</small>`, csv: "items_viewed" },
        { key: "views", label: "Views", num: true, cls: "viewscell", render: viewsCell },
        { key: "downloads", label: "Downloads", num: true, render: (r) => fmt(r.downloads) },
        { key: "avg_engagement_s", label: "Avg. engagement", num: true, render: (r) => dur(r.avg_engagement_s) },
        { key: "landing_views", label: "Landing page views", num: true, render: (r) => fmt(r.landing_views) },
      ],
    });
    dataTable(document.getElementById("items"), {
      label: "items", rows: s.items, sort: "views", searchKey: "label", columns: itemColumns, pageSize: 25, csvName: "top-items.csv",
    });
  }

  async function collection(uuid) {
    const d = await load(`data/collections/${uuid}.json`);
    document.title = `${d.label} · Digital Collections Analytics`;
    const crumbs = [`<a href="#/">All digital objects</a>`]
      .concat(d.parents.map((p) => `<a href="#/c/${p.uuid}">${esc(p.label)}</a>`)).join(" › ");
    main.innerHTML = `${banner(d)}
      <div class="crumbs">${crumbs}</div>
      <h2 class="page-title">${esc(d.label)}</h2>
      <p class="meta"><a href="${esc(d.url)}">View collection ↗</a> · ${rangeText(d)}</p>
      ${tiles([
        { label: "Page views", value: fmt(d.views), sub: "member items + landing pages" },
        { label: "Items viewed", value: `${fmt(d.items_viewed)}<small style="font-size:1rem;font-weight:400"> / ${d.items_total_capped ? "≥ " : ""}${fmt(d.items_total)}</small>`, sub: d.items_total_capped ? "the site lists at most 1,000 members, so the total is a minimum" : `${pct(d.items_viewed, d.items_total)} of the collection` },
        { label: "Downloads", value: fmt(d.downloads) },
        { label: "Avg. engagement", value: dur(d.avg_engagement_s), sub: "per page view" },
        { label: "Landing page views", value: fmt(d.landing_views), sub: (d.landing_pages || []).length ? `/do/ page + ${(d.landing_pages).map((u) => esc(u.replace(/^https?:\/\/[^/]+/, ""))).join(", ")}` : "the collection page itself" },
      ])}
      ${d.subcollections.length ? `<section class="panel"><h3>Sub-collections</h3><p>${d.subcollections.map((c) => `<a href="#/c/${c.uuid}">${esc(c.label)}</a>`).join(" · ")}</p><p class="note">Totals on this page include items in sub-collections.</p></section>` : ""}
      <section class="panel"><h3>Views per month</h3><div class="chart" id="trend"></div>${monthTable(fillMonths(d.monthly))}</section>
      ${trafficPanels(d)}
      <section class="panel"><h3>Items</h3><div id="items"></div></section>`;
    lineChart(document.getElementById("trend"), fillMonths(d.monthly));
    dataTable(document.getElementById("items"), {
      label: "items", rows: d.items, sort: "views", searchKey: "label", zeroToggle: true, columns: itemColumns,
      csvName: `${d.label.replace(/[^\w]+/g, "-").toLowerCase()}-items.csv`,
    });
  }

  async function group(id) {
    const d = await load(`data/groups/${id}.json`);
    document.title = `${d.label} · Digital Collections Analytics`;
    const full = d.detail === "full";
    main.innerHTML = `${banner(d)}
      <div class="crumbs"><a href="#/">All digital objects</a> › ${esc(d.kind)}</div>
      <h2 class="page-title">${esc(d.label)}</h2>
      <p class="meta"><a href="${esc(d.url)}">Visit ↗</a> · ${rangeText(d)}</p>
      ${tiles([
        { label: "Page views", value: fmt(d.views) },
        { label: "Users", value: fmt(d.users), sub: "de-duplicated across the section" },
        ...(full ? [{ label: "Pages viewed", value: fmt(d.items_viewed) }, { label: "Downloads", value: fmt(d.downloads) }] : []),
        { label: "Avg. engagement", value: dur(d.avg_engagement_s), sub: "per page view" },
      ])}
      <section class="panel"><h3>Views per month</h3><div class="chart" id="trend"></div>${monthTable(d.monthly)}</section>
      ${full ? trafficPanels(d) : ""}
      <section class="panel"><h3>Pages</h3><div id="pages"></div>
        ${full ? "" : `<p class="note">Views and users per page; users can’t be added up across pages. Search terms (query strings) aren’t part of these paths.</p>`}</section>`;
    lineChart(document.getElementById("trend"), d.monthly, (p) => `<br><b>${fmt(p.users)}</b> users`);
    if (full) {
      dataTable(document.getElementById("pages"), {
        label: "pages", rows: d.items, sort: "views", searchKey: "label", columns: [{ ...itemColumns[0], label: "Page" }, ...itemColumns.slice(1)],
        csvName: `${d.id}-pages.csv`,
      });
    } else {
      dataTable(document.getElementById("pages"), {
        label: "pages", rows: d.pages, sort: "views", csvName: `${d.id}-pages.csv`,
        columns: [
          { key: "path", label: "Page", render: (r) => `<a href="${esc(r.url)}">${esc(r.path)}</a>`, csv: ["path", "url"] },
          { key: "views", label: "Views", num: true, cls: "viewscell", render: viewsCell },
          { key: "users", label: "Users", num: true, render: (r) => fmt(r.users) },
        ],
      });
    }
  }

  async function route() {
    redraws = [];
    const m = location.hash.match(/^#\/c\/([0-9a-f-]{36})$/i);
    const g = location.hash.match(/^#\/g\/([\w-]+)$/);
    try {
      await (m ? collection(m[1].toLowerCase()) : g ? group(g[1]) : overview());
    } catch (err) {
      main.innerHTML = `<p class="empty">Could not load data (${esc(err.message)}). Has the harvester been run?</p>`;
    }
    const d = await load("data/index.json").catch(() => null);
    if (d) {
      document.getElementById("site-link").href = d.base_url;
      document.getElementById("footer").innerHTML =
        `Source: Google Analytics 4 page views of <a href="${esc(d.base_url)}">${esc(d.base_url.replace(/^https?:\/\//, ""))}</a>/do/ pages, joined to collection membership from the site’s IIIF metadata. Pages of books, albums and other compound objects are counted under their parent object. Users are counted per page and can’t be added up across pages or items.`;
    }
    main.focus({ preventScroll: true });
    window.scrollTo(0, 0);
  }

  let t;
  window.addEventListener("resize", () => { clearTimeout(t); t = setTimeout(() => redraws.forEach((f) => f()), 150); });
  window.addEventListener("hashchange", route);
  route();
})();
