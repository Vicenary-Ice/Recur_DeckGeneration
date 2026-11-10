"""The contract between the AI layer and the deterministic deck builder.

The LLM must return exactly this structure. For Anthropic structured outputs pass the model to the SDK
(`client.messages.parse(..., output_format=DeckContent)`) or send `anthropic.transform_schema(DeckContent)`
(NOT the raw `model_json_schema()`, which contains keywords the API rejects). Validation here is structural;
text-fit and style checks live in qa.py.

Thin markets: validate with `DeckContent.model_validate(data, context={"min_competitors": 3})`.
"""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator

from .axes import AXIS_KINDS, infer_kind
from .spec import MAX_COMPETITORS, MAX_PER_CELL, MIN_COMPETITORS, TARGET_CELL

AxisKind = Literal["customer_size", "customer_complexity", "product_scope", "industry_depth", "deployment_modernity",
                   "segment_focus"]
assert set(AxisKind.__args__) == set(AXIS_KINDS)  # axes.py is the single source of the kinds and their directions


def _fill(data: Any, **defaults: Any) -> Any:
    """Content saved before a field existed: fill it, so old content.json files (fixtures, replays) still load.
    The fields stay required in the JSON schema, so the writer must always answer them."""
    if isinstance(data, dict):
        missing = {k: v() if callable(v) else v for k, v in defaults.items() if k not in data}
        if missing:
            return {**data, **missing}
    return data


def _no_newline(v: str) -> str:
    if "\n" in v or "\r" in v:
        raise ValueError("line breaks are only allowed in y-axis labels")
    return v.strip()


def _norm_domain(d: str) -> str:
    d = re.sub(r"^https?://", "", d.strip().lower()).split("/")[0]
    return d[4:] if d.startswith("www.") else d


class Company(BaseModel):
    display_name: str = Field(description="Brand exactly as the company writes it, e.g. 'Ai2000'")
    domain: str = Field(description="Verified primary domain, e.g. 'a2000software.com'")
    aliases: list[str] = Field(default_factory=list, description="Former / alternate names, e.g. ['A2000']")

    @field_validator("display_name", "domain")
    @classmethod
    def check_no_newline(cls, v: str) -> str:
        return _no_newline(v)


class ThesisPoint(BaseModel):
    headline: str = Field(description="One line, about 6-10 words, plain declarative sentence, no full stop")
    subline: str = Field(description="One line, about 10-14 words, the concrete reason, no full stop")
    evidence_ids: list[str] = Field(default_factory=list)

    @field_validator("headline", "subline")
    @classmethod
    def check_no_newline(cls, v: str) -> str:
        return _no_newline(v)


class Axis(BaseModel):
    kind: AxisKind | None = Field(description="Which ordinal scale this axis is, from the fixed list; low_label is its "
                                              "low end (left / bottom), high_label its high end (right / top)")
    title: str = Field(description="Axis name, 1-3 words, sentence case, e.g. 'Customer size'")
    low_label: str = Field(description="Left (x) or bottom (y) half. Only y labels may use '\\n' (max 2 lines)")
    high_label: str = Field(description="Right (x) or top (y) half. The target sits in high/high")

    @model_validator(mode="before")
    @classmethod
    def _legacy(cls, data: Any) -> Any:
        if isinstance(data, dict) and "kind" not in data:  # written before axes had a kind: read it from the title
            return {**data, "kind": infer_kind(data.get("title", ""), data.get("low_label", ""), data.get("high_label", ""))}
        return data

    @field_validator("title")
    @classmethod
    def check_no_newline(cls, v: str) -> str:
        return _no_newline(v)


class Competitor(BaseModel):
    name: str
    domain: str
    x: Literal["left", "right"]
    y: Literal["bottom", "top"]
    rationale: str = Field(description="One sentence: why it sits in this quadrant, from what the evidence states "
                                       "(never from what a page does not mention)")
    evidence_ids: list[str] = Field(default_factory=list)
    x_evidence_ids: list[str] = Field(description="Ids of the quotes that place it on this side of the x axis (>= 1); "
                                                  "prefer its own site or a neutral directory")
    y_evidence_ids: list[str] = Field(description="Ids of the quotes that place it on this side of the y axis (>= 1); "
                                                  "prefer its own site or a neutral directory")

    @model_validator(mode="before")
    @classmethod
    def _legacy(cls, data: Any) -> Any:
        return _fill(data, x_evidence_ids=list, y_evidence_ids=list)

    @field_validator("name", "domain")
    @classmethod
    def check_no_newline(cls, v: str) -> str:
        return _no_newline(v)


class OurTake(BaseModel):
    headline: str = Field(description="Bold claim, <= 14 words, starts with the company name")
    points: list[str] = Field(min_length=2, max_length=2,
                              description="Two short reasons competitor GROUPS fall short, <= 10 words each")
    evidence_ids: list[str] = Field(default_factory=list)
    point_evidence_ids: list[list[str]] = Field(description="One list per point (same order), each with >= 1 id of a "
                                                            "quote showing that group really lacks what the point says")

    @model_validator(mode="before")
    @classmethod
    def _legacy(cls, data: Any) -> Any:
        return _fill(data, point_evidence_ids=list)

    @field_validator("headline")
    @classmethod
    def _h(cls, v: str) -> str:
        return _no_newline(v)

    @field_validator("points")
    @classmethod
    def _p(cls, v: list[str]) -> list[str]:
        return [_no_newline(x) for x in v]


class Market(BaseModel):
    subtitle: str = Field(description="One line: the customer need that defines the market, <= 15 words")
    subtitle_evidence_ids: list[str] = Field(default_factory=list)
    x_axis: Axis
    y_axis: Axis
    competitors: list[Competitor]
    our_take: OurTake

    @field_validator("subtitle")
    @classmethod
    def _s(cls, v: str) -> str:
        return _no_newline(v)

    @field_validator("x_axis")
    @classmethod
    def _x(cls, v: Axis) -> Axis:
        _no_newline(v.low_label), _no_newline(v.high_label)
        return v

    @field_validator("y_axis")
    @classmethod
    def _y(cls, v: Axis) -> Axis:
        for lab in (v.low_label, v.high_label):
            if lab.count("\n") > 1:
                raise ValueError("y-axis labels may use at most 2 lines")
        return v

    @field_validator("competitors")
    @classmethod
    def _count(cls, v: list[Competitor], info: ValidationInfo) -> list[Competitor]:
        from .logos import slug
        lo = (info.context or {}).get("min_competitors", MIN_COMPETITORS)
        if not lo <= len(v) <= MAX_COMPETITORS:
            raise ValueError(f"need {lo}-{MAX_COMPETITORS} competitors, got {len(v)}")
        slugs = [slug(c.name) for c in v]
        if len(set(slugs)) != len(slugs):
            raise ValueError("duplicate competitor names (after normalising)")
        doms = [_norm_domain(c.domain) for c in v]
        if len(set(doms)) != len(doms):
            raise ValueError("duplicate competitor domains")
        return v

    @model_validator(mode="after")
    def _cells(self) -> "Market":
        counts: dict[tuple[str, str], int] = {}
        for c in self.competitors:
            counts[(c.x, c.y)] = counts.get((c.x, c.y), 0) + 1
        counts[TARGET_CELL] = counts.get(TARGET_CELL, 0) + 1  # the target itself
        over = {k: n for k, n in counts.items() if n > MAX_PER_CELL}
        if over:
            raise ValueError(f"too many logos in cells {over}; max {MAX_PER_CELL} per cell incl. target")
        if len(counts) < 3:
            raise ValueError("competitors must spread across at least 3 of the 4 quadrants (incl. target's)")
        return self


Lever = Literal["ai_native_gtm", "ai_rebuild", "ai_modernization", "built_the_bench"]


class DeckContent(BaseModel):
    company: Company
    thesis: list[ThesisPoint] = Field(min_length=3, max_length=3,
                                      description="[0]=WHY WE'RE HERE (what changed in the customers' world), "
                                                  "[1]=WHY WE'RE EXCITED (why this business is durable), "
                                                  "[2]=HOW WE CAN HELP (the Recur lever that fits an evidenced gap)")
    lever: Lever | None = Field(description="The Recur lever point 03 offers; one of the allowed_levers")
    lever_gap_evidence_ids: list[str] = Field(description="Ids of the quotes that show the gap this lever fixes (>= 1)")
    market: Market

    @model_validator(mode="before")
    @classmethod
    def _legacy(cls, data: Any) -> Any:
        return _fill(data, lever=None, lever_gap_evidence_ids=list)

    @model_validator(mode="after")
    def _target_not_competitor(self) -> "DeckContent":
        from .logos import slug
        names = {slug(n) for n in [self.company.display_name, *self.company.aliases]}
        dom = _norm_domain(self.company.domain)
        for c in self.market.competitors:
            if slug(c.name) in names or _norm_domain(c.domain) == dom:
                raise ValueError(f"competitor '{c.name}' is the target company itself")
        return self
