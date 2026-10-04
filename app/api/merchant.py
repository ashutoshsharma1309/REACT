"""REACT Merchant endpoints: profile and onboarding, baselines, sales, value metrics. Thin: logic in the domain pack."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.domains import MERCHANT, current_domain

router = APIRouter(tags=["merchant"])


def _require_merchant(db: Session) -> None:
    if current_domain(db) != MERCHANT:
        raise HTTPException(409, "this dataset is not a REACT Merchant business (system_state.domain != merchant)")


class MerchantSetup(BaseModel):
    business_name: str = Field(min_length=2, max_length=80)
    business_type: str = "electronics"
    city: str = Field("Bengaluru", max_length=60)
    state: str = Field("Karnataka", max_length=60)
    locality: str = Field("Jayanagar", max_length=60)
    store_count: int = Field(1, ge=1, le=3)
    categories: list[str] = Field(default_factory=list)


@router.get("/merchant", summary="The merchant: profile, stores, categories, data window (all synthetic)")
def get_merchant(db: Session = Depends(get_db)):
    from app.domains.merchant import read_models

    _require_merchant(db)
    return read_models.merchant(db)


@router.get("/merchant/templates", summary="Business types available for onboarding and their product categories")
def merchant_templates():
    from app.domains.merchant import read_models

    return read_models.templates()


@router.post("/merchant/setup", summary="Onboarding: generate a synthetic demo business from the profile (replaces the current data)")
def merchant_setup(payload: MerchantSetup, db: Session = Depends(get_db)):
    from app.detection import DetectionEngine
    from app.domains.merchant import read_models
    from app.domains.merchant.profile import ProfileError
    from app.domains.merchant.simulation import regenerate
    from app.opportunities import OpportunityEngine

    try:
        regenerate(db, profile=payload.model_dump())
    except ProfileError as exc:
        raise HTTPException(422, str(exc)) from exc
    DetectionEngine(db).run()
    OpportunityEngine(db).run()
    return read_models.merchant(db)


@router.get("/business-baseline", summary="What is normal for this merchant, computed from its own records")
def business_baseline(db: Session = Depends(get_db)):
    from app.domains.merchant import baseline

    _require_merchant(db)
    return baseline.compute(db)


@router.get("/sales", summary="Sales, margin, channels, payment methods, categories, zones and expenses")
def sales(days: int = Query(28, ge=7, le=120), db: Session = Depends(get_db)):
    from app.domains.merchant import read_models

    _require_merchant(db)
    return read_models.sales(db, days)


@router.get("/metrics/value", summary="What REACT surfaced and what the merchant decided (estimated / potential / simulated)")
def value_metrics(db: Session = Depends(get_db)):
    from app.domains.merchant import read_models

    _require_merchant(db)
    return read_models.value_metrics(db)
