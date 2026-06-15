"""
opencontractsau CLI

Commands:
    qld     Scrape Queensland TMR contract disclosure data
    nsw     Scrape NSW contract award data (historical or live)
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.logging import RichHandler

app = typer.Typer(
    name="opencontractsau",
    help="OC4IDS-compliant scraper for Australian government procurement data.",
    no_args_is_help=True,
)
nsw_app = typer.Typer(help="NSW contract award data.")
app.add_typer(nsw_app, name="nsw")

qld_app = typer.Typer(help="Queensland contract award data.")
app.add_typer(qld_app, name="qld")

console = Console()


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        handlers=[RichHandler(console=console, rich_tracebacks=True)],
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def _write_output(package, output: Path | None) -> None:
    data = package.model_dump(by_alias=True, mode="json", exclude_none=True)

    def _serialise(obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        raise TypeError(f"Not serialisable: {type(obj)}")

    payload = json.dumps(data, indent=2, default=_serialise)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload, encoding="utf-8")
        console.print(f"[green]Wrote {len(package.releases)} releases to {output}[/green]")
    else:
        sys.stdout.write(payload)
        sys.stdout.write("\n")


@app.command()
def act(
    where: Annotated[
        Optional[str],
        typer.Option("--where", help="SoQL WHERE clause, e.g. \"execution_date > '2024-01-01'\""),
    ] = None,
    max_records: Annotated[
        Optional[int],
        typer.Option("--max-records", help="Cap on total records (default: all)"),
    ] = None,
    output: Annotated[
        Optional[Path],
        typer.Option("--output", "-o", help="Output path (default: stdout)"),
    ] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Scrape the ACT Contracts Register from data.act.gov.au (Socrata)."""
    _setup_logging(verbose)
    from opencontractsau.scrapers.act.scraper import scrape

    package = asyncio.run(scrape(where=where, max_records=max_records))
    console.print(f"[cyan]ACT:[/cyan] {len(package.releases)} releases")
    _write_output(package, output)


@qld_app.command("tmr")
def qld_tmr(
    year: Annotated[
        Optional[list[str]],
        typer.Option("--year", "-y", help="Financial year(s) to fetch, e.g. 2024-2025"),
    ] = None,
    all_years: Annotated[
        bool,
        typer.Option("--all", help="Fetch all available years (slow)"),
    ] = False,
    output: Annotated[
        Optional[Path],
        typer.Option("--output", "-o", help="Output path (default: stdout)"),
    ] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Scrape Queensland TMR contract disclosures from data.qld.gov.au."""
    _setup_logging(verbose)
    from opencontractsau.scrapers.qld.tmr import scrape

    package = asyncio.run(scrape(years=list(year) if year else None, all_years=all_years))
    console.print(f"[cyan]QLD TMR:[/cyan] {len(package.releases)} releases")
    _write_output(package, output)


@qld_app.command("ckan")
def qld_ckan(
    only_agency: Annotated[
        Optional[list[str]],
        typer.Option("--only", help="Restrict to specific agency code(s) e.g. qfd, treasury"),
    ] = None,
    skip_agency: Annotated[
        Optional[list[str]],
        typer.Option("--skip", help="Skip specific agency code(s)"),
    ] = None,
    most_recent_only: Annotated[
        bool,
        typer.Option("--most-recent-only", help="Per agency, only fetch the most recent FY"),
    ] = False,
    output: Annotated[
        Optional[Path],
        typer.Option("--output", "-o", help="Output path (default: stdout)"),
    ] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Harvest QLD multi-agency contract disclosure data from data.qld.gov.au."""
    _setup_logging(verbose)
    from opencontractsau.scrapers.qld.ckan import scrape

    package = asyncio.run(
        scrape(
            only_agencies=list(only_agency) if only_agency else None,
            skip_agencies=list(skip_agency) if skip_agency else None,
            most_recent_only=most_recent_only,
        )
    )
    console.print(f"[cyan]QLD CKAN:[/cyan] {len(package.releases)} releases")
    _write_output(package, output)


@app.command()
def vic(
    preset: Annotated[str, typer.Option("--preset")] = "recentlyAwarded",
    max_pages: Annotated[int, typer.Option("--max-pages")] = 20,
    output: Annotated[Optional[Path], typer.Option("--output", "-o")] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Scrape Victoria recently-awarded contracts (tenders.vic.gov.au)."""
    _setup_logging(verbose)
    from opencontractsau.scrapers.vic.scraper import scrape

    package = asyncio.run(scrape(preset=preset, max_pages=max_pages))
    console.print(f"[cyan]VIC:[/cyan] {len(package.releases)} releases")
    _write_output(package, output)


@app.command()
def nt(
    mode: Annotated[str, typer.Option("--mode", help="recent | range")] = "recent",
    start_id: Annotated[Optional[int], typer.Option("--start-id")] = None,
    end_id: Annotated[Optional[int], typer.Option("--end-id")] = None,
    max_list_pages: Annotated[
        int,
        typer.Option("--max-list-pages", help="Cap pages in recent mode"),
    ] = 20,
    checkpoint: Annotated[Optional[Path], typer.Option("--checkpoint")] = None,
    output: Annotated[Optional[Path], typer.Option("--output", "-o")] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Scrape NT QTOL awarded tenders (tendersonline.nt.gov.au)."""
    _setup_logging(verbose)
    from opencontractsau.scrapers.nt.scraper import scrape

    package = asyncio.run(
        scrape(
            mode=mode,
            start_id=start_id,
            end_id=end_id,
            max_list_pages=max_list_pages,
            checkpoint_file=checkpoint,
        )
    )
    console.print(f"[cyan]NT:[/cyan] {len(package.releases)} releases")
    _write_output(package, output)


@app.command()
def tas(
    mode: Annotated[
        str,
        typer.Option("--mode", help="recent | range | backfill"),
    ] = "recent",
    start_id: Annotated[
        Optional[int],
        typer.Option("--start-id", help="First contract ID (range/backfill)"),
    ] = None,
    end_id: Annotated[
        Optional[int],
        typer.Option("--end-id", help="Last contract ID (range/backfill)"),
    ] = None,
    checkpoint: Annotated[
        Optional[Path],
        typer.Option("--checkpoint", help="Resume support: append-only file of completed IDs"),
    ] = None,
    output: Annotated[
        Optional[Path],
        typer.Option("--output", "-o", help="Output path (default: stdout)"),
    ] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Scrape Tasmania eTenders contract award details (tenders.tas.gov.au)."""
    _setup_logging(verbose)
    from opencontractsau.scrapers.tas.scraper import scrape

    package = asyncio.run(
        scrape(
            mode=mode,
            start_id=start_id,
            end_id=end_id,
            checkpoint_file=checkpoint,
        )
    )
    console.print(f"[cyan]TAS:[/cyan] {len(package.releases)} releases")
    _write_output(package, output)


@nsw_app.command("historical")
def nsw_historical(
    local_path: Annotated[
        Optional[Path],
        typer.Option(
            "--local-path",
            "-f",
            help="Path to locally-downloaded OCP archive (.zip or .json)",
        ),
    ] = None,
    output: Annotated[
        Optional[Path],
        typer.Option("--output", "-o", help="Output path (default: stdout)"),
    ] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """
    Import NSW historical OCDS data (2005-02/2025) from the OCP archive.

    If --local-path is not provided, auto-discovery of the OCP bulk download
    URL is attempted. If that fails, download the archive manually from:
    https://data.open-contracting.org/en/publication/11
    """
    _setup_logging(verbose)
    from opencontractsau.scrapers.nsw.historical import scrape

    try:
        package = asyncio.run(scrape(local_path=local_path))
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    console.print(f"[cyan]NSW historical:[/cyan] {len(package.releases)} releases")
    _write_output(package, output)


@nsw_app.command("live")
def nsw_live(
    from_date: Annotated[
        Optional[str],
        typer.Option("--from", help="Start date ISO-8601 or DD/MM/YYYY"),
    ] = None,
    to_date: Annotated[
        Optional[str],
        typer.Option("--to", help="End date ISO-8601 or DD/MM/YYYY"),
    ] = None,
    max_pages: Annotated[int, typer.Option("--max-pages", help="Safety cap on pages")] = 50,
    output: Annotated[
        Optional[Path],
        typer.Option("--output", "-o", help="Output path (default: stdout)"),
    ] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """
    Scrape live NSW contract award notices from buy.nsw.gov.au.

    Note: buy.nsw blocks non-browser user-agents. If 403 errors occur,
    the HTML parser fallback may need a Playwright-based driver. See
    CONTRIBUTING.md for details.
    """
    _setup_logging(verbose)
    from opencontractsau.scrapers.nsw.live import scrape
    from opencontractsau.transformers.qld import _parse_au_date

    def _parse_date(s: str | None) -> datetime | None:
        if not s:
            return None
        for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
        raise typer.BadParameter(f"Cannot parse date: {s}")

    package = asyncio.run(
        scrape(
            from_date=_parse_date(from_date),
            to_date=_parse_date(to_date),
            max_pages=max_pages,
        )
    )
    console.print(f"[cyan]NSW live:[/cyan] {len(package.releases)} releases")
    _write_output(package, output)


if __name__ == "__main__":
    app()
