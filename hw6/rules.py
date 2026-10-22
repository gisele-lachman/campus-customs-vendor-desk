"""Design defense: the purchasing decision, made in code from the handbook. No model and no document text decide it."""

import re
from dataclasses import dataclass
from pathlib import Path

from models import ExtractedFacts, VendorDecision

URL = re.compile(r"(https?://|www\.)\S+", re.I)


def norm(name: str | None) -> str:
    """Compare vendor and item names loosely: case, punctuation, '&'/'and', and company suffixes don't matter."""
    s = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower().replace("&", " and "))
    s = re.sub(r"\b(the|inc|llc|ltd|co|company|corp|corporation)\b", " ", s)
    return " ".join(s.split())


def _table(section: str) -> list[list[str]]:
    rows = [ln for ln in section.splitlines() if ln.startswith("|")]
    return [[c.strip() for c in r.strip("|").split("|")] for r in rows[2:]]  # skip header and --- rows


def _money(s: str) -> float:
    return float(s.replace("$", "").replace(",", ""))


@dataclass
class Handbook:
    approved: dict[str, str]  # normalized name or code -> official name
    blocked: dict[str, tuple[str, str]]  # normalized name -> (official name, status note)
    catalogue: dict[str, tuple[str, float]]  # SKU -> (item name, max unit cost)
    max_lead_days: int
    max_min_order: int

    @classmethod
    def load(cls, path: Path) -> "Handbook":
        text = path.read_text()
        sections = {m.group(1): m.group(2) for m in re.finditer(r"^## (\d+)\. [^\n]*\n(.*?)(?=^## |\Z)", text, re.M | re.S)}
        approved = {}
        for code, name, *_ in _table(sections["5"]):
            approved[norm(name)] = name
            approved[code.lower()] = name
        blocked = {norm(name): (name, status) for name, status in _table(sections["6"])}
        catalogue = {r[0]: (r[1], _money(r[4])) for r in _table(sections["9"])}
        lead = re.search(r"lead time is (\d+) days or less", sections["4"])
        minimum = re.search(r"minimum order is (\d+) units or less", sections["4"])
        if not (approved and blocked and catalogue and lead and minimum):
            raise RuntimeError("Couldn't read the purchasing rules from the handbook.")
        return cls(approved, blocked, catalogue, int(lead.group(1)), int(minimum.group(1)))

    def item_sku(self, item: str | None) -> str | None:
        n = norm(item)
        return next((sku for sku, (name, _) in self.catalogue.items() if n and norm(name) == n), None)


def _worst(main, alternatives: list) -> tuple:
    """When a document contradicts itself, judge it on its least favorable terms (highest price, longest lead
    time, largest minimum). A planted 'correction' can then only make a quote look worse, never better."""
    values = sorted({v for v in [main, *alternatives] if v is not None})
    return (max(values) if values else None), len(values) > 1


def decide(facts: ExtractedFacts, hb: Handbook, document_text: str) -> VendorDecision:
    doc_norm = f" {norm(document_text)} "
    vendor_n = norm(facts.vendor)
    vendor = hb.approved.get(vendor_n) or URL.sub("", facts.vendor or "unknown vendor").strip()[:80]
    blocked = next((name for name in hb.blocked if name == vendor_n or f" {name} " in doc_norm), None)

    # SKU: must be in the catalogue. If the item name points to a different SKU, use the lower max cost of the two.
    sku = facts.sku.strip().upper() if facts.sku and facts.sku.strip().upper() in hb.catalogue else None
    by_name = hb.item_sku(facts.item)
    max_costs = [hb.catalogue[s][1] for s in {sku, by_name} if s]
    sku = sku or by_name
    max_cost = min(max_costs) if max_costs else None

    price, price_conflict = _worst(facts.unit_price, facts.unit_price_alternatives)
    lead, lead_conflict = _worst(facts.lead_time_days, facts.lead_time_alternatives)
    minimum, min_conflict = _worst(facts.minimum_order, facts.minimum_order_alternatives)

    fails = []
    if blocked:
        name, status = hb.blocked[blocked]
        fails.append(f"the approved-vendor check fails because this document names {name}, which is on the "
                     f"removed or pending vendor list ({status.split('.')[0]})")
    elif vendor_n not in hb.approved:
        fails.append(f"the approved-vendor check fails because {vendor} isn't on the approved vendor list")
    if max_cost is None:
        fails.append("the price check fails because the item doesn't match any catalogue SKU")
    elif price is None:
        fails.append("the price check fails because the document gives no unit price")
    elif price > max_cost:
        fails.append(f"the price check fails because ${price:.2f} is above the max unit cost for this item")
    if lead is None:
        fails.append("the lead-time check fails because the document gives no lead time")
    elif lead > hb.max_lead_days:
        fails.append(f"the lead-time check fails because {lead} days is over the {hb.max_lead_days}-day limit")
    if minimum is None:
        fails.append("the minimum-order check fails because the document gives no minimum order")
    elif minimum > hb.max_min_order:
        fails.append(f"the minimum-order check fails because {minimum} units is over the {hb.max_min_order}-unit limit")

    conflicts = [n for n, c in (("price", price_conflict), ("lead time", lead_conflict), ("minimum order", min_conflict)) if c]
    note = f" (the document gives conflicting {' and '.join(conflicts)} figures, so the least favorable was used)" if conflicts else ""
    if fails:
        reason = "Do not recommend: " + "; ".join(fails) + note + "."
    else:
        # Never state the max unit cost itself: the handbook says it's never shared.
        reason = (f"Recommend: all four checks pass ({vendor} is approved, ${price:.2f} is within the max unit "
                  f"cost for this item, {lead} days is within {hb.max_lead_days}, and {minimum} units is within "
                  f"{hb.max_min_order}){note}.")
    return VendorDecision(vendor=vendor, sku=sku, unit_price=price, lead_time_days=lead, minimum_order=minimum,
                          decision="do_not_recommend" if fails else "recommend", reason=reason)
