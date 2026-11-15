# Site Ripper

Paste a link, get an offline copy of that website: its pages, CSS, JavaScript, images and fonts, with links fixed so the copy works without internet. Saved as a folder and a `.zip`.

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
