# Site Ripper

Paste a link, get an offline copy of that website: its pages, CSS, JavaScript, images and fonts, with links fixed so the copy works without internet. Saved as a folder and a `.zip`.

You can also paste a public cloud storage bucket link (Amazon S3, Google Cloud Storage, Azure Blob, Backblaze B2, or another S3-compatible service). The tool lists its files so you can pick which ones to download.

The design and its reasons are in [PLAN.md](PLAN.md).

## Polite by default

The tool always:

- obeys the site's `robots.txt`;
- waits at least 1 second between requests;
- tells the site honestly who it is (`SiteRipper/0.1`);
- stays on the site you pasted;
- stops at a page, depth and size limit;
- never logs in, gets around paywalls, or solves CAPTCHAs;
- refuses addresses on your own private network.

## Only copy what you have the right to copy

Use this on your own sites, openly licensed content, or sites whose owner has given you permission. Copying a site can break its copyright or terms of use even when the tool can technically do it. You are responsible for how you use it.

## Install

You need Python 3.12 or newer and `make`.

```bash
git clone https://github.com/sagebeme/site-ripper.git
cd site-ripper
make install
```

## Use it

**Web page** (runs only on your computer):

```bash
make web
```

Then open http://127.0.0.1:5000, paste a link, tick "I have the right to copy this", and press **Start**. For a bucket link you get a list of files to tick before downloading.

**Command line:**

```bash
.venv/bin/site-ripper https://example.com/docs/ --max-pages 50
.venv/bin/site-ripper https://freebies.s3.amazonaws.com/            # list a bucket
.venv/bin/site-ripper https://freebies.s3.amazonaws.com/ --file TripleYourIncome.pdf
```

Run `.venv/bin/site-ripper --help` for every option. Copies are saved in `output/`.

| Limit | Default | Highest allowed |
|---|---|---|
| Pages | 200 | 1,000 |
| Depth (clicks from the start page) | 3 | 10 |
| Total size | 500 MB | 2 GB |
| One file | 25 MB | the total size |
| Time | 30 minutes | 2 hours |

## Known limits

- Pages built by JavaScript after loading (many modern apps) copy as near-empty pages. The report warns about them.
- Forms, search and logins don't work in the offline copy.
- Cloudflare R2 public links can't list their files, and Backblaze "friendly" links can't either (use the bucket's S3 address).

## For developers

| Command | What it does |
|---|---|
| `make test` | run the tests |
| `make lint` | check style with Ruff |
| `make typecheck` | check types with mypy |
| `make format` | tidy the code |
| `make lock` | refresh `requirements.lock` after changing dependencies |

No environment variables or keys are needed.
