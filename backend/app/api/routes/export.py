from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.api.deps import get_current_user
from app.db.database import get_db
from app.models.models import User, AgentRun
from app.services.export_service import to_csv, to_excel
from app.services.ocr_service import text_to_docx_bytes, text_to_pdf_bytes

router = APIRouter(prefix="/export", tags=["export"])


class TextExportRequest(BaseModel):
    text: str = Field(min_length=1)
    filename: str = Field(default="export", max_length=200)


# Plain-text export (Summary panel's "Word"/"PDF" download, and anywhere
# else that only has result text in the browser, no server-side job file).
# Not job-scoped or auth-checked against a specific resource — the text is
# whatever the authenticated caller already has in front of them, so this
# just converts it, the same trust level as the client-side .txt download
# it sits next to.
@router.post("/text/docx")
async def export_text_docx(body: TextExportRequest, user: User = Depends(get_current_user)):
    docx_bytes = text_to_docx_bytes(body.text)
    filename = f"{body.filename}.docx"
    return Response(
        content=docx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.post("/text/pdf")
async def export_text_pdf(body: TextExportRequest, user: User = Depends(get_current_user)):
    pdf_bytes = text_to_pdf_bytes(body.text)
    filename = f"{body.filename}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/agent/{run_id}/csv")
async def export_csv(run_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    result = await db.execute(select(AgentRun).where(AgentRun.id == run_id, AgentRun.user_id == user.id))
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Agent run not found")
    if not run.structured_result:
        raise HTTPException(status_code=400, detail="No structured result to export")

    csv_bytes = to_csv(run.structured_result)
    filename = f"{run.pipeline_type}_{run.id[:8]}.csv"
    return Response(content=csv_bytes, media_type="text/csv", headers={"Content-Disposition": f"attachment; filename={filename}"})


@router.get("/agent/{run_id}/excel")
async def export_excel(run_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    result = await db.execute(select(AgentRun).where(AgentRun.id == run_id, AgentRun.user_id == user.id))
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Agent run not found")
    if not run.structured_result:
        raise HTTPException(status_code=400, detail="No structured result to export")

    xlsx_bytes = to_excel(run.structured_result, run.pipeline_type)
    filename = f"{run.pipeline_type}_{run.id[:8]}.xlsx"
    return Response(
        content=xlsx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )