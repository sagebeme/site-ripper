# Site Ripper

Paste a link, get an offline copy of that website: its pages, CSS, JavaScript, images and fonts, with links fixed so the copy works without internet. Saved as a folder and a `.zip`.

You can also paste a public cloud storage bucket link (Amazon S3, Google Cloud Storage, Azure Blob, Backblaze B2, or another S3-compatible service). The tool lists its files so you can pick which ones to download.

**Status:** planning. Nothing to install yet. The full plan is in [PLAN.md](PLAN.md).

## Polite by default

The tool always:

- obeys the site's `robots.txt`;
- waits at least 1 second between requests;
- tells the site honestly who it is (`SiteRipper/0.1`);
- stays on the site you pasted;
- stops at a page, depth and size limit;
- never logs in, gets around paywalls, or solves CAPTCHAs.

## Only copy what you have the right to copy

Use this on your own sites, openly licensed content, or sites whose owner has given you permission. Copying a site can break its copyright or terms of use even when the tool can technically do it. You are responsible for how you use it.
