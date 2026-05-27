# au-procurement

**OC4IDS-compliant scraper for Australian government procurement data.**

Every Australian state legally requires contract awards to be disclosed. Not one requires them in machine-readable form. This project produces OCDS release packages from publicly-available, legally-mandated contract award data across Australian jurisdictions.

Free to use. No API key. No login.

Repo: [github.com/demitonapp/opencontractau](https://github.com/demitonapp/opencontractau)

---

## What it does

Scrapes government tender portals, normalises the data to [OCDS v1.1](https://standard.open-contracting.org/latest/en/) release package format, and writes JSON output. The data is then usable by any OCDS-compatible system, including platforms that implement [OC4IDS](https://standard.open-contracting.org/infrastructure/latest/en/) project records.

## Jurisdictions

| Jurisdiction | Source | Status | ABN published | Notes |
|---|---|---|---|---|
| Commonwealth | AusTender OCDS API | Use upstream | Yes | Already OCDS - consume `api.tenders.gov.au/ocds` directly |
| **ACT** | data.act.gov.au (Socrata) | Live | Yes | 2374 contracts on first run |
| **NSW historical** | OCP archive (NSW eTendering) | Beta | Yes (legacy) | Frozen 2005 - Feb 2025 |
| **NSW live** | buy.nsw.gov.au | Beta | Partial | 2025-present |
| **NT** | tendersonline.nt.gov.au | Live | No | Recent + range mode; supplier ABN not published |
| **QLD TMR** | data.qld.gov.au CKAN CSV | Live | Yes | Transport and Main Roads, 2019-present |
| **QLD multi-agency** | data.qld.gov.au CKAN CSV | Live | Partial (9%) | 8 agencies inc. Health, Treasury, QBCC |
| **TAS** | tenders.tas.gov.au | Live | No | Sequential ID walk; supplier ABN not published |
| **VIC** | tenders.vic.gov.au | Live | Yes (64%) | Recently-awarded; requires Chrome TLS impersonation |
| SA | contracts.sa.gov.au | Deferred | n/a | Search form requires JavaScript - needs browser driver |
| QLD QTenders | qtenders.hpw.qld.gov.au | Deferred | n/a | Blazor WebAssembly SPA - needs browser driver |
| WA | tenders.wa.gov.au | Documented gap | n/a | See [LEGAL.md](LEGAL.md) for the robots.txt policy |

> **ABN-published column** is observed coverage on a recent sample, not a statement about each jurisdiction's legal obligations. Several states legally require ABN disclosure but agencies do not consistently populate the field. The procurement transparency story is largely about this gap.

> **AusTender note:** The Commonwealth already publishes OCDS at `api.tenders.gov.au/ocds`. Consume that directly rather than re-scraping it.

> **Deferred jurisdictions** (SA, QLD QTenders) are not blocked by us - they simply need a browser runtime (Playwright) to render the search interface. That driver is on the roadmap; the parsers and OCDS mapping are in place. PRs welcome.

## Installation

Requires Python 3.11+.

```bash
pip install au-procurement
# or with uv:
uv pip install au-procurement
```

## Usage

```bash
# Queensland - most recent financial year
au-procurement qld --output output/qld.json

# Queensland - specific year
au-procurement qld --year 2024-2025 --output output/qld-2024-25.json

# Queensland - all available years
au-procurement qld --all --output output/qld-all.json

# NSW historical archive (2005-Feb 2025)
# Download the archive from https://data.open-contracting.org/en/publication/11
# then run:
au-procurement nsw historical --local-path /path/to/archive.zip --output output/nsw-historical.json

# NSW live (buy.nsw.gov.au)
au-procurement nsw live --output output/nsw-live.json
au-procurement nsw live --from 2025-01-01 --to 2025-06-30 --output output/nsw-live-h1.json

# All output goes to stdout by default (pipe-friendly)
au-procurement qld | jq '.releases[] | .awards[].value.amount'
```

## Output format

Each command produces an [OCDS Release Package](https://standard.open-contracting.org/latest/en/schema/release_package/):

```json
{
  "version": "1.1",
  "publishedDate": "2026-05-21T00:00:00",
  "publisher": {
    "name": "OpenContractAU",
    "uid": "https://github.com/demitonapp/opencontractau"
  },
  "license": "https://creativecommons.org/licenses/by/4.0/",
  "releases": [
    {
      "ocid": "ocau-qld-tmr-TMR-2019-001",
      "id": "ocau-qld-tmr-TMR-2019-001-award-20190521-1",
      "date": "2019-05-21T00:00:00",
      "tag": ["award"],
      "buyer": { "name": "Transport and Main Roads" },
      "awards": [{
        "value": { "amount": 875619, "currency": "AUD" },
        "suppliers": [{
          "name": "TUFF YARDS PTY LTD",
          "identifier": { "scheme": "AU-ABN", "id": "12345678901" }
        }]
      }]
    }
  ]
}
```

## Is this legal?

Yes. See the full legal analysis in the blog post that launched this project: [Australia's $70 billion procurement transparency problem](https://demiton.io/blog/australia-procurement-transparency-gap).

Short version: scraping publicly-disclosed, legally-mandated business data is not personal information under the Privacy Act (it concerns businesses and ABNs, not individuals). The portals are not restricted data under the Criminal Code because they are public and unauthenticated. Copyright does not protect facts under Australian law ([IceTV v Nine Network [2009] HCA 14](https://www.austlii.edu.au/cgi-bin/viewdoc/au/cases/cth/HCA/2009/14.html)).

This project:
- Identifies itself with a descriptive User-Agent (`OpenContractAU/0.x`)
- Respects `robots.txt` for every target host
- Rate-limits to one request per three seconds on live scrapers
- Honours takedown requests (open a GitHub issue)

## Self-hosting

This repository publishes the *code*, not the data.

To use the normalised corpus without running your own scrapers, sign up at
[demiton.io](https://demiton.io) (free Public tier - no credit card required).
The Public tier exposes the full indexed corpus via the REST API and MCP.

To run the scrapers yourself:

```bash
git clone https://github.com/demitonapp/opencontractau
cd opencontractau
pip install -e .       # or: uv pip install -e .

# CLI - write OCDS JSON to a file
au-procurement qld --output output/qld.json
au-procurement qld --all --output output/qld-all.json

# Python API - use in your own code
import asyncio
from au_procurement import fetch_releases

package = asyncio.run(fetch_releases("ACT"))
print(f"Fetched {len(package.releases)} releases")

# Filter by date (supported on ACT and NSW_LIVE)
from datetime import date
package = asyncio.run(fetch_releases("ACT", since=date(2025, 1, 1)))
```

Available jurisdiction keys for the Python API:

| Key | Jurisdiction |
|---|---|
| `ACT` | Australian Capital Territory (Socrata) |
| `QLD_TMR` | Queensland TMR contract disclosure |
| `QLD_MULTI` | Queensland multi-agency (8 agencies) |
| `NSW_LIVE` | New South Wales live (buy.nsw.gov.au) |
| `NSW_HISTORICAL` | New South Wales historical archive (2005-2025) |
| `NT` | Northern Territory |
| `TAS` | Tasmania |
| `VIC` | Victoria (requires Chrome TLS impersonation via curl_cffi) |

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). NSW and Queensland are the first jurisdictions; Victoria, South Australia, Western Australia, ACT, Northern Territory, and Tasmania follow.

## Relation to Demiton

[Demiton](https://demiton.io) is the primary operational consumer of this library. It vendors
au-procurement as a Python dependency and indexes the OCDS output into its market intelligence
corpus. When this library supports a jurisdiction, Demiton removes its own state-specific scraping
code and consumes the community data through the same `fetch_releases()` API that anyone else can use.

The corpus output is not published as bulk downloadable artifacts. The legitimate public consumption
surface is Demiton's [Public tier](https://demiton.io) (rate-limited API and MCP, free registration).

## License

Apache 2.0. Data outputs are CC-BY-4.0.
