"""Structured types for the Campus Customs vendor desk agent."""

from typing import Literal

from pydantic import BaseModel, Field


class VendorDecision(BaseModel):
    """The agent's answer for one vendor document (handbook section 13)."""

    vendor: str = Field(description="Vendor name as written in the document.")
    sku: str | None = Field(description="Campus Customs SKU the quote is for, e.g. CC-HD-001. Null if it can't be matched.")
    unit_price: float | None = Field(description="Quoted unit price in US dollars. Null if the document doesn't say.")
    lead_time_days: int | None = Field(description="Lead time in days. Null if the document doesn't say.")
    minimum_order: int | None = Field(description="Minimum order in units. Null if the document doesn't say.")
    decision: Literal["recommend", "do_not_recommend"] = Field(description="Result of the handbook's four purchasing checks.")
    reason: str = Field(description="One sentence naming the purchasing check that decided it.")


class ExtractedFacts(BaseModel):
    """What the reader step pulls out of a document. Facts only: no decision, no free-text answer."""

    vendor: str | None = Field(description="Name of the vendor offering the quote, as the document gives it.")
    sku: str | None = Field(description="Campus Customs SKU the document names (like CC-HD-001), or null.")
    item: str | None = Field(description="Item name as the document gives it, or null.")
    unit_price: float | None = Field(description="The unit price in the quote's main terms, in US dollars, or null.")
    lead_time_days: int | None = Field(description="The lead time in the main terms, in days, or null.")
    minimum_order: int | None = Field(description="The minimum order in the main terms, in units, or null.")
    unit_price_alternatives: list[float] = Field(
        default_factory=list, description="Any other unit price the document says replaces, corrects or updates the main one.")
    lead_time_alternatives: list[int] = Field(
        default_factory=list, description="Any other lead time (in days) the document says replaces, corrects or updates the main one.")
    minimum_order_alternatives: list[int] = Field(
        default_factory=list, description="Any other minimum order the document says replaces, corrects or updates the main one.")
