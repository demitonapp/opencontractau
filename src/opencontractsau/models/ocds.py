"""
OCDS v1.1 Pydantic models (simplified subset).

OC4IDS wraps OCDS contracting processes inside project records. For
jurisdictions where we have contract-level data only (no project hierarchy),
we emit OCDS release packages directly. Demiton assembles these into OC4IDS
project records internally using corridor and project-context mapping.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


class Identifier(BaseModel):
    scheme: str
    id: str
    legal_name: str | None = Field(None, alias="legalName")
    uri: str | None = None

    model_config = {"populate_by_name": True}


class Address(BaseModel):
    street_address: str | None = Field(None, alias="streetAddress")
    locality: str | None = None
    region: str | None = None
    postal_code: str | None = Field(None, alias="postalCode")
    country_name: str = Field("Australia", alias="countryName")

    model_config = {"populate_by_name": True}


class Organization(BaseModel):
    id: str
    name: str
    identifier: Identifier | None = None
    address: Address | None = None
    roles: list[str] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class Value(BaseModel):
    amount: Decimal | None = None
    currency: str = "AUD"


class Period(BaseModel):
    start_date: datetime | None = Field(None, alias="startDate")
    end_date: datetime | None = Field(None, alias="endDate")

    model_config = {"populate_by_name": True}


class Tender(BaseModel):
    id: str
    title: str | None = None
    status: str = "complete"
    procurement_method: str | None = Field(None, alias="procurementMethod")
    procurement_method_details: str | None = Field(None, alias="procurementMethodDetails")
    procurement_method_rationale: str | None = Field(None, alias="procurementMethodRationale")
    number_of_tenderers: int | None = Field(None, alias="numberOfTenderers")
    value: Value | None = None
    contract_period: Period | None = Field(None, alias="contractPeriod")

    model_config = {"populate_by_name": True}


class Award(BaseModel):
    id: str
    title: str | None = None
    description: str | None = None
    status: str = "active"
    date: datetime | None = None
    value: Value | None = None
    suppliers: list[Organization] = Field(default_factory=list)
    contract_period: Period | None = Field(None, alias="contractPeriod")

    model_config = {"populate_by_name": True}


class Contract(BaseModel):
    id: str
    award_id: str = Field(..., alias="awardID")
    title: str | None = None
    status: str = "active"
    value: Value | None = None
    date_signed: datetime | None = Field(None, alias="dateSigned")
    period: Period | None = None

    model_config = {"populate_by_name": True}


class Release(BaseModel):
    ocid: str
    id: str
    date: datetime
    tag: list[str] = Field(default_factory=lambda: ["award"])
    initiation_type: str = Field("tender", alias="initiationType")
    language: str = "en"
    buyer: Organization | None = None
    tender: Tender | None = None
    awards: list[Award] = Field(default_factory=list)
    contracts: list[Contract] = Field(default_factory=list)
    source: dict[str, Any] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}


class Publisher(BaseModel):
    name: str = "OpenContractsAU"
    scheme: str = "GitHub"
    uid: str = "https://github.com/demitonapp/opencontractsau"
    uri: str = "https://data.demiton.io/au-contracts/"


_CDN_BASE = "https://data.demiton.io/au-contracts"


class ReleasePackage(BaseModel):
    uri: str | None = None
    version: str = "1.1"
    published_date: datetime = Field(..., alias="publishedDate")
    publisher: Publisher = Field(default_factory=Publisher)
    releases: list[Release] = Field(default_factory=list)
    license: str = "https://creativecommons.org/licenses/by/4.0/"

    model_config = {"populate_by_name": True}

    @classmethod
    def with_jurisdiction(cls, jurisdiction: str, **kwargs) -> "ReleasePackage":
        """Construct a package with its CDN uri pre-set for the given jurisdiction key."""
        return cls(uri=f"{_CDN_BASE}/{jurisdiction}.json", **kwargs)
