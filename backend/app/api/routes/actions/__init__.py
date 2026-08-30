from fastapi import APIRouter

from app.api.routes.actions.runs import router as runs_router
from app.api.routes.actions.approvals import router as approvals_router
from app.api.routes.actions.catalog import router as catalog_router

router = APIRouter()

# Catalog (no auth on /catalog, auth on /agent-run/{id}/available)
router.include_router(catalog_router, tags=["Action Catalog"])

# Action run lifecycle
router.include_router(runs_router, tags=["Action Runs"])

# SSE stream + approval/rejection
router.include_router(approvals_router, tags=["Action Approvals"])
