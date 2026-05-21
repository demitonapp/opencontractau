# au-procurement

**OC4IDS-compliant scraper for Australian government procurement data.**

Every Australian state legally requires contract awards to be disclosed. Not one requires them in machine-readable form. This project produces OCDS release packages from publicly-available, legally-mandated contract award data across Australian jurisdictions.

Free to use. No API key. No login.

Repo: [github.com/demitonapp/au-procurement](https://github.com/demitonapp/au-procurement)

---

## What it does

Scrapes government tender portals, normalises the data to [OCDS v1.1](https://standard.open-contracting.org/latest/en/) release package format, and writes JSON output. The data is then usable by any OCDS-compatible system, including platforms that implement [OC4IDS](https://standard.open-contracting.org/infrastructure/latest/en/) project records.

## Jurisdictions

| Jurisdiction | Source | Status | Years |
|---|---|---|---|
| Queensland (TMR) | data.qld.gov.au CKAN CSV | Live | 2020-present |
| NSW (historical) | OCP archive (NSW eTendering) | Live | 2005-02/2025 |
| NSW (live) | buy.nsw.gov.au | Beta | 2025-present |
| Commonwealth | AusTender OCDS API | See note | All |

> **AusTender note:** The Commonwealth already publishes OCDS at `api.tenders.gov.au/ocds`. Consume that directly rather than re-scraping it.

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
    "name": "OpenContractsAU",
    "uid": "https://github.com/demitonapp/au-procurement"
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
- Identifies itself with a descriptive User-Agent (`OpenContractsAU/0.x`)
- Respects `robots.txt` for every target host
- Rate-limits to one request per three seconds on live scrapers
- Honours takedown requests (open a GitHub issue)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). NSW and Queensland are the first jurisdictions; Victoria, South Australia, Western Australia, ACT, Northern Territory, and Tasmania follow.

## Relation to Demiton

[Demiton](https://demiton.io) is the first consumer of this dataset. When this open source feed covers a jurisdiction, Demiton removes its own state-specific scraping code and consumes the community data like everyone else.

## License

Apache 2.0. Data outputs are CC-BY-4.0.
