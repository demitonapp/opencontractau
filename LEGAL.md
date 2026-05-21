# LEGAL.md

This file documents the legal posture of OpenContractsAU and records explicit decisions on jurisdictions where the legal landscape is non-trivial.

## Default posture

Every scraper in this repository operates against publicly-disclosed, legally-mandated contract award data. Specifically:

- The data is **not personal information** under the [Privacy Act 1988 (Cth)](https://www.legislation.gov.au/C2004A03712/latest) - it concerns businesses, ABNs, and contract values, not natural persons.
- The portals are **not restricted data** under the [Criminal Code Act 1995 (Cth)](https://www.legislation.gov.au/C2004A04868/latest) - they are public and unauthenticated.
- The 2024 statutory tort of serious invasion of privacy (commenced 10 June 2025) **does not apply** - there is no reasonable expectation of privacy in legally-required public disclosures, and section 7 of the Privacy Act expressly covers conduct authorised by law.
- **Facts are not copyrightable** in Australia following [IceTV Pty Ltd v Nine Network Australia Pty Ltd [2009] HCA 14](https://www.austlii.edu.au/cgi-bin/viewdoc/au/cases/cth/HCA/2009/14.html).

Every scraper:

- Identifies as `OpenContractsAU/0.x (+https://github.com/demitonapp/au-procurement)`.
- Rate-limits to one request per three seconds (live scrapers).
- Respects `robots.txt` for the target host **except where explicitly documented below**.
- Honours takedown requests - open a [GitHub issue](https://github.com/demitonapp/au-procurement/issues).

## Jurisdiction-specific notes

### Western Australia - `tenders.wa.gov.au`

**Status: not scraped. Robots.txt blocks the disclosure path.**

WA's contract award disclosure portal is the only state portal in Australia that uses `robots.txt` to disallow access to its mandated-disclosure pages:

```
User-agent: *
Allow: /watenders/index.do
Disallow: /
```

The contracts on those pages are legally-mandated public disclosures under the [Procurement Act 2020 (WA)](https://www.legislation.wa.gov.au/legislation/statutes.nsf/main_mrtitle_14056_homepage.html). The data is public. The data is required by law to be public. WA simply blocks crawlers from accessing what every other state publishes openly.

We have chosen **not** to scrape WA in this release.

The reasoning is narrative rather than legal:
- Building a WA scraper that ignores `robots.txt` is technically defensible (mandated public disclosure, identified UA, low rate). Multiple academic and journalistic scrapers have done this without consequence.
- But the cleaner story is to **leave the gap visible**. WA is the only state that blocks open access to its own mandated transparency. That is itself the finding.

If WA changes its `robots.txt`, this scraper will ship within a week.

### Northern Territory - `tendersonline.nt.gov.au`

The portal returned `ECONNREFUSED` when fetching `/robots.txt` during the initial probe. The scraper assumes "no restrictions" for the disclosure path and operates with `OpenContractsAU` UA at 3-second intervals. If a `robots.txt` is added that disallows our path, the scraper will respect it on the next run.

### Tasmania - `tenders.tas.gov.au`

The portal returned HTTP 500 when fetching `/robots.txt` during the initial probe. Same posture as NT - operates with `OpenContractsAU` UA and 3-second rate limiting until a valid `robots.txt` appears.

### Victoria, South Australia, Queensland QTenders

These portals sit behind Cloudflare and use TLS-fingerprint bot detection that rejects ordinary HTTP clients. The Victoria scraper uses `curl_cffi` to impersonate Chrome's TLS handshake. This is **not** a robots.txt bypass - VIC's `robots.txt` permits the `/contract/` path with a 2-second crawl delay (we use 3). The TLS impersonation only bypasses Cloudflare's anti-bot check.

`contracts.sa.gov.au` and `qtenders.hpw.qld.gov.au` are deferred because their search UIs require JavaScript execution to render (form-driven and Blazor WebAssembly respectively). Both will land when a Playwright-backed driver is added; the OCDS parsers and transformers are already in place.

## Reporting a concern

If you are a government agency or rights-holder with a takedown or scope concern:

1. Open a [GitHub issue](https://github.com/demitonapp/au-procurement/issues) describing the concern.
2. The scraper for that jurisdiction will be paused within 48 hours pending resolution.
3. Substantive engagement is welcome - this project exists because the public-interest case for OCDS-format procurement transparency is strong, but it is not adversarial.
