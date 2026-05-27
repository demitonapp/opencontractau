# Contributing to au-procurement

Contributors are welcome. The goal is one well-maintained scraper per Australian jurisdiction, producing OCDS v1.1 release packages from publicly-disclosed contract award data.

## Non-negotiable rules

Every scraper in this repo must:

1. **Identify itself** with a User-Agent string in the format `OpenContractAU/0.x (+https://github.com/demitonapp/opencontractau)`.
2. **Respect robots.txt** for the target host. Use `au_procurement.scrapers.base.RateLimitedClient`, which checks robots.txt automatically.
3. **Rate-limit** to one request per three seconds on live scrapers. CKAN/static-file downloads may use a shorter interval (1 second minimum).
4. **Honour takedown requests.** If a government agency requests removal of data, open an issue immediately and pause that scraper pending resolution.
5. **Scrape only public, unauthenticated endpoints.** No login bypass, no cookie injection, no credentials.
6. **Produce valid OCDS v1.1** output via the `ReleasePackage` model. Run the tests before opening a PR.

## Adding a new jurisdiction

1. Create `src/au_procurement/scrapers/{jurisdiction}/scraper.py`.
2. Implement an async `scrape(...) -> ReleasePackage` function.
3. Add the jurisdiction to `src/au_procurement/cli.py` as a new subcommand.
4. Write transformer tests in `tests/test_{jurisdiction}_transformer.py`.
5. Update `README.md` jurisdiction table.
6. Open a PR with the jurisdiction name in the title.

## Running tests

```bash
uv pip install -e ".[dev]"
pytest
```

## OCID prefix

The current prefix `ocau` is provisional. An OCP-registered prefix will be assigned when the Open Contracting Partnership approves the publisher registration. Do not change the prefix without a corresponding PR updating the OCID prefix constant in `transformers/`.

## Data quality

- ABNs must be validated as 11-digit strings (strip spaces, reject invalid length).
- Dates must be normalised to ISO-8601 with UTC assumption where timezone is absent.
- Currency is always AUD; do not add a conversion layer.
- Contract values are stored as `Decimal`, not `float`, to avoid rounding errors.

## What not to scrape

- Personal information (names of individual public servants as contract managers are acceptable; names of individual tenderers who did not win are not).
- Data behind authentication.
- Data the source explicitly marks as confidential.
- Procurement data that is legally exempt from disclosure (e.g. national security contracts explicitly withheld by the agency).
