# Site Ripper: build plan

**Status: plan only. No code yet. Waiting for approval.**

## What it does

You paste a link (for example `https://example.com/docs/`). The tool visits that page, follows the links to other pages on the same site, and saves a copy of everything you need to read the site offline: the HTML pages, CSS, JavaScript, images and fonts. It rewrites the links inside the copy so they point at the saved files, not the live site. At the end you get a folder and a `.zip` you can open with no internet.

Think of it as a friendly, small version of `wget --mirror` or HTTrack, for people who are just starting out.

## Ground rules (built in, not optional)

These are defaults the tool always applies. There is no switch to turn them off.

| Rule | What it means in plain words |
|---|---|
| Obey `robots.txt` | Every site can publish a `robots.txt` file saying which parts robots may not visit. The tool reads it first and skips anything it forbids. If the file says "wait N seconds between visits" (`Crawl-delay`), the tool waits at least that long. |
| Polite delay | At least **1 second** between requests to the site, one request at a time. This stops the tool from hammering someone's server. |
| Honest name | Every request says who it is: `SiteRipper/0.1 (+https://github.com/sagebeme/site-ripper)`. It never pretends to be a normal browser. |
| No logins or paywalls | It sends no cookies, passwords or tokens. If a page answers "401", "403" or "402" (not allowed / pay first), the tool notes it and moves on. It never tries to get around it. |
| No CAPTCHA solving | If a page shows a CAPTCHA or "are you a robot?" check, the tool stops crawling that site and tells you why. |
| Size caps | Defaults: **200 pages**, **depth 3**, **500 MB total**, **25 MB per file**, **30 minutes**. You can lower these. Raising them has a hard ceiling (1,000 pages, 2 GB). |
| Same site only | It only follows links on the same host you pasted. Assets (images, CSS, fonts) may come from a CDN, but pages never do. |
| Your responsibility | The README and the UI both say: only copy sites you have the right to copy (your own site, openly licensed content, or with the owner's permission). |

## The stack (small on purpose)

- **Python 3.12**: easy to read, and good tools exist for this job.
- **`httpx`**: downloads pages and files. Supports timeouts and redirects cleanly.
- **`beautifulsoup4`**: reads HTML so we can find links and change them.
- **`urllib.robotparser`** (built into Python): reads `robots.txt`.
- **`zipfile`** (built into Python): makes the zip.
- **Flask**: a tiny local web page with a text box and a progress list. Runs only on your own computer (`127.0.0.1`), never on the internet.
- **Tooling**: `uv` to install things, Ruff for lint/format, mypy for type checks, pytest for tests.

That's four outside packages. No database, no browser engine.

## How it is laid out

```
site-ripper/
  src/site_ripper/
    crawler.py     # the queue: which page to visit next, depth, limits
    fetcher.py     # downloads one URL politely (delay, robots, name, size cap)
    rewriter.py    # finds links in HTML/CSS and changes them to local paths
    paths.py       # turns a URL into a safe file path on disk
    packager.py    # makes the zip
    cli.py         # `site-ripper https://example.com`
    web.py         # the Flask page
  tests/           # pytest, with a tiny fake site served locally
  output/          # where copies go (gitignored)
```

## How crawling works

1. **Check the link.** Only `http://` and `https://`. Refuse `localhost`, private IP ranges (`10.x`, `192.168.x`, etc.) and `file://`, so the tool can't be pointed at your own network by accident.
2. **Read `robots.txt`** for that site. If the start page itself is forbidden, stop and say so.
3. **Start a queue** with the start page at depth 0.
4. **Loop**, one URL at a time:
   - skip it if already seen, forbidden by robots, off-site, or deeper than the depth limit;
   - wait the polite delay;
   - download it (with the size cap and a 20-second timeout);
   - if it is HTML: save it, collect its page links (to the queue at depth + 1) and its asset links (images, CSS, JS, fonts, icons);
   - if it is CSS: also collect `url(...)` and `@import` links inside it (fonts and background images live there).
5. **Stop** when the queue is empty or any cap is hit. The report says which.

"Depth" means how many clicks away from the start page. Depth 0 is the page you pasted, depth 1 is pages it links to, and so on.

**Scope tip:** if you paste `https://example.com/docs/`, it stays inside `/docs/` by default. A checkbox lets you widen it to the whole host.

## Assets and link rewriting

- **Which links:** `<a href>`, `<link href>` (CSS, icons), `<script src>`, `<img src>` and `srcset`, `<source>`, `<video poster>`, and `url(...)` / `@import` inside CSS and `style="..."`.
- **Where files go:** `output/<host>/<path>`. Examples:
  - `https://example.com/docs/` → `example.com/docs/index.html`
  - `https://example.com/about` → `example.com/about/index.html`
  - `https://cdn.example.net/a.css?v=3` → `_assets/cdn.example.net/a_v=3.css`
  - Odd characters are made safe and paths can never climb out of the output folder (`..` is blocked).
- **Rewriting:** every link to something we saved becomes a *relative* path (like `../css/site.css`) so the copy works from any folder and from inside the zip. Links to things we didn't save (other sites, forbidden pages) are left pointing at the live web, and get a small note in the report.
- **Not handled in v1:** content that JavaScript builds after the page loads. Many modern sites (single-page apps) will copy as mostly-empty pages. The report will warn when a page looks like this.

## Output folder and zip

```
output/example.com-2026-11-16T1430/
  example.com/...        # the pages
  _assets/...            # files from other hosts (CDNs)
  REPORT.md              # what was saved, skipped, and why
  manifest.json          # URL → local file, status, size
example.com-2026-11-16T1430.zip
```

`REPORT.md` lists: pages saved, assets saved, total size, things skipped (robots, off-site, too big, errors, blocked by login/paywall), and which cap stopped the crawl.

## The UI

A single local page at `http://127.0.0.1:5000`:

1. A text box: "Paste a link". A tickbox: "I have the right to copy this site" (must be ticked to start).
2. Optional fields for page limit and depth (pre-filled with the defaults).
3. A **Start** button. Below it, a live list: "Saved /docs/intro (12 KB)", "Skipped /admin (robots.txt)", plus a counter and a progress bar against the page cap.
4. A **Stop** button that ends the crawl cleanly and still packages what was saved.
5. When done: a **Download zip** button and the report.

Progress is sent with Server-Sent Events (the server pushes small messages to the page as they happen). One crawl at a time.

There is also a command-line version: `site-ripper https://example.com --max-pages 50`.

## Build order (once approved)

1. Project skeleton: `uv init`, Ruff, mypy, pytest, `.gitignore`, one passing test.
2. `paths.py` (URL → safe file path) with tests.
3. `fetcher.py` (robots, delay, honest name, size cap, refuse private IPs) with tests against a local fake site.
4. `crawler.py` (queue, depth, scope, caps) with tests.
5. `rewriter.py` (HTML + CSS link rewriting) with tests.
6. `packager.py` + `REPORT.md` + `manifest.json`.
7. `cli.py`. First end-to-end run against the local fake site.
8. `web.py` (the Flask page with live progress).
9. README usage section, then a real test on a site we own or that allows it.

Each step is its own commit on a `feat/` branch.

## Risks and limits

- **Legal / ethical:** copying a site can break copyright or terms of use even when it's technically possible. The tool's defaults keep it polite, but the person using it is responsible for having the right to copy. We won't add features whose purpose is getting around a site's protections.
- **JavaScript-heavy sites** won't copy well (see above). Adding a headless browser later is possible but makes the tool much bigger, so it's out of scope for v1.
- **Forms, search boxes, logins, comments** won't work offline. It's a read-only snapshot.
- **Endless pages** (calendars, "?page=9999", filters) can create infinite links. The caps stop this; we also skip URLs with very long query strings.
- **Big files** (videos, large PDFs) are skipped above the per-file cap and listed in the report.
- **Being blocked:** some sites rate-limit or block crawlers. On HTTP 429 ("too many requests") or 503, the tool waits longer (respecting `Retry-After`), and gives up on the site after repeated refusals instead of pushing harder.
- **Pointing it at private networks:** blocked by refusing local and private addresses (also checked after redirects).
- **Disk space:** the total-size cap protects it.

## Open questions for you

1. Is Python + Flask OK, or would you prefer JavaScript (Node)?
2. Are the default caps (200 pages, depth 3, 1 s delay) sensible for what you want to copy?
3. Do you want the command-line version too, or only the web page?
