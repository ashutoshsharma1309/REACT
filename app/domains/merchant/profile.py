"""Merchant profile: what the onboarding form captures. It parameterises the synthetic business, nothing more.

There is no account system. The profile is stored on `system_state.profile["merchant"]` so a reset or a demo scenario
regenerates the same business.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from app.domains.merchant.catalog import CITY_LOCALITIES, ZONE_DIRECTIONS, get_template, localities

MAX_STORES = 3


class ProfileError(ValueError):
    pass


@dataclass
class MerchantProfile:
    business_name: str = "Astra Retail"
    business_type: str = "electronics"  # template key
    city: str = "Bengaluru"
    state: str = "Karnataka"
    locality: str = "Jayanagar"
    store_count: int = 1
    categories: list[str] = field(default_factory=list)  # category codes; empty = every category of the template

    def normalised(self) -> "MerchantProfile":
        tpl = get_template(self.business_type)
        name = " ".join((self.business_name or "").split())[:80]
        if len(name) < 2:
            raise ProfileError("business name must have at least 2 characters")
        city = " ".join((self.city or "").split())[:60] or "Bengaluru"
        state = " ".join((self.state or "").split())[:60] or "Karnataka"
        if not 1 <= int(self.store_count) <= MAX_STORES:
            raise ProfileError(f"number of stores must be between 1 and {MAX_STORES}")
        valid = [c.code for c in tpl.categories]
        cats = [c for c in valid if c in set(self.categories or valid)]
        unknown = set(self.categories or []) - set(valid)
        if unknown:
            raise ProfileError(f"unknown categories for {tpl.label}: {', '.join(sorted(unknown))}")
        if len(cats) < 3:
            raise ProfileError("choose at least 3 product categories")
        locality = " ".join((self.locality or "").split())[:60] or localities(city, "South")[0]
        return MerchantProfile(name, tpl.key, city, state, locality, int(self.store_count), cats)

    def store_zone(self) -> str:
        """Zone (direction) of the main store, inferred from the locality when the city is known."""
        for direction, places in CITY_LOCALITIES.get(self.city, {}).items():
            if self.locality in places:
                return direction
        return "South"

    def zones(self) -> list[str]:
        return [f"{d} {self.city}" for d in ZONE_DIRECTIONS]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "MerchantProfile":
        data = data or {}
        allowed = {k: data[k] for k in ("business_name", "business_type", "city", "state", "locality", "store_count", "categories") if k in data and data[k] is not None}
        return cls(**allowed).normalised()
