"""
Shared scraper base for the TenderSearch Java platform.

TenderSearch is a procurement portal product used by multiple Australian
state governments. Deployments confirmed to use this platform as of
2026-05-21:

- VIC: tenders.vic.gov.au       (live, works with curl_cffi)
- SA:  contracts.sa.gov.au      (form-driven, requires JS - deferred)
- WA:  tenders.wa.gov.au        (CSRF nonce + robots.txt block - skipped)

All deployments share the same list/detail HTML structure:
- List page: <tr id="contractRow{id}"> with links to /contract/view?id={id}
- Detail page: <span class="LIST_TITLE">Label</span> + <div class="col-sm-8">Value</div>
- Supplier block: <tr class="contractor"> with <b>Name</b> + nested ABN/ACN table

Cloudflare is fronting these portals. ``client.py`` uses curl_cffi with
Chrome TLS impersonation to pass the bot check.
"""
