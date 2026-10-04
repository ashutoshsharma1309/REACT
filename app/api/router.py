from __future__ import annotations

from fastapi import APIRouter

from app.api import agents, decisions, entities, insights, investigations, merchant, operations, product, simulation, system

api_router = APIRouter()
api_router.include_router(system.router)
api_router.include_router(entities.router)
api_router.include_router(insights.router)
api_router.include_router(simulation.router)
api_router.include_router(investigations.router)
api_router.include_router(agents.router)
api_router.include_router(decisions.router)
api_router.include_router(product.router)
api_router.include_router(operations.router)
api_router.include_router(merchant.router)
