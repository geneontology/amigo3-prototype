"""Engine-neutral query contract inspired by linkml-store's collection API."""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Filter(StrictModel):
    id: str
    field: str
    op: Literal["eq", "in", "not_in", "gte", "lte", "between", "exists"] = "in"
    values: list[str | bool | int | float] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def check_values(self):
        if self.op == "exists":
            if self.values:
                raise ValueError("exists takes no values")
        elif not self.values or (self.op in ("eq", "gte", "lte") and len(self.values) != 1):
            raise ValueError(f"Invalid number of values for {self.op}")
        elif self.op == "between" and (
            len(self.values) != 2
            or any(type(v) is not int for v in self.values)
            or self.values[0] > self.values[1]
        ):
            raise ValueError("between takes an inclusive [minimum, maximum] integer pair")
        return self


class Facet(StrictModel):
    field: str
    limit: int = Field(default=15, ge=1, le=1000)
    exclude_filters: list[str] = Field(default_factory=list)
    # Explicit buckets for lazy hierarchy navigation, including zero-hit values.
    # When supplied, these replace the top-N request (and its limit).
    values: list[str | bool | int] | None = Field(default=None, min_length=1, max_length=200)


class Page(StrictModel):
    size: int = Field(default=30, ge=1, le=100)
    cursor: str | None = None


class Sort(StrictModel):
    """Sort a scalar field, or rank an exact value/membership as a boolean."""

    field: str
    direction: Literal["asc", "desc"] = "asc"
    value: str | bool | int | None = None


class Breakdown(StrictModel):
    """A bounded chart over facetable categories with an optional scalar split."""

    field: str
    split_by: str | None = None
    limit: int = Field(default=12, ge=1, le=20)

    @model_validator(mode="after")
    def distinct_dimensions(self):
        if self.field == self.split_by:
            raise ValueError("Breakdown dimensions must be different")
        return self


class Histogram(StrictModel):
    """Binned distribution of an integer field, optionally joint with a second one.

    Bins are computed from the exact value distribution of all matching records, so
    both engines agree; log scale suits skewed counts such as model sizes.
    """

    field: str
    y_field: str | None = None
    bins: int = Field(default=12, ge=1, le=40)
    y_bins: int = Field(default=8, ge=1, le=20)
    scale: Literal["linear", "log"] = "linear"
    y_scale: Literal["linear", "log"] = "linear"
    exclude_filters: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def distinct_dimensions(self):
        if self.field == self.y_field:
            raise ValueError("Histogram dimensions must be different")
        return self


Collection = Literal[
    "genes",
    "annotations",
    "terms",
    "gocams",
    "modules",
    "families",
    "reports",
    "projects",
    "rules",
    "reactions",
    "chemicals",
    "reaction_participants",
    "cell_types",
    "differentiations",
    "references",
]


class Query(StrictModel):
    collection: Collection = "genes"
    view: str | None = None
    search: str = Field(default="", max_length=500)
    filters: list[Filter] = Field(default_factory=list, max_length=30)
    facets: dict[str, Facet] = Field(default_factory=dict, max_length=20)
    breakdowns: dict[str, Breakdown] = Field(default_factory=dict, max_length=3)
    histograms: dict[str, Histogram] = Field(default_factory=dict, max_length=24)
    select: list[str] | None = None
    # Named collection presets or generic clauses; empty preserves API ID order.
    sort: str | list[Sort] = Field(default_factory=list)
    page: Page = Field(default_factory=Page)

    @model_validator(mode="after")
    def unique_filters(self):
        if len(self.sort) > (80 if isinstance(self.sort, str) else 8):
            raise ValueError("Too many sort clauses or invalid sort name")
        names = [f.id for f in self.filters]
        if len(set(names)) != len(names):
            raise ValueError("Filter IDs must be unique")
        if any(
            set(f.exclude_filters) - set(names) for f in [*self.facets.values(), *self.histograms.values()]
        ):
            raise ValueError("Facet exclusion references an unknown filter")
        return self

    def signature(self):
        value = self.model_dump(exclude={"page", "view"})
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:20]


def encode_cursor(build: str, query: Query, position: str | int) -> str:
    return base64.urlsafe_b64encode(json.dumps([build, query.signature(), position]).encode()).decode()


def decode_cursor(build: str, query: Query) -> str | int | None:
    if not query.page.cursor:
        return None
    try:
        cursor_build, signature, position = json.loads(base64.urlsafe_b64decode(query.page.cursor))
    except Exception as error:
        raise ValueError("Invalid cursor") from error
    if cursor_build != build or signature != query.signature():
        raise ValueError("Cursor belongs to a different query or build")
    return position
