from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.internal.db import get_async_session
from open_webui.utils import agent_memory
from open_webui.utils.agent_memory_consolidation import run_agent_memory_consolidation_jobs_once
from open_webui.utils.agent_memory_extraction import run_agent_memory_extraction_jobs_once
from open_webui.utils.agent_memory_workers import get_agent_memory_job_metrics
from open_webui.utils.auth import get_admin_user

router = APIRouter()


class RetryFailedJobsForm(BaseModel):
    user_id: str | None = None


class RunJobsForm(BaseModel):
    limit: int | None = None


class RebuildIndexForm(BaseModel):
    user_id: str
    scope_type: Literal["global", "folder"] | None = None
    scope_id: str | None = None

    @model_validator(mode="after")
    def validate_scope(self):
        if self.scope_type == "folder" and not (self.scope_id or "").strip():
            raise ValueError("scope_id is required when scope_type is folder")
        return self


class ResetAgentMemoryForm(BaseModel):
    user_id: str
    note_mode: Literal["convert", "delete"] = "convert"
    scope_type: Literal["global", "folder"] | None = None
    scope_id: str | None = None

    @model_validator(mode="after")
    def validate_scope(self):
        if self.scope_type == "folder" and not (self.scope_id or "").strip():
            raise ValueError("scope_id is required when scope_type is folder")
        return self


class ClearAgentMemoryForm(ResetAgentMemoryForm):
    pass


@router.get("/jobs/failed")
async def get_failed_jobs(
    user_id: str | None = None,
    user=Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_session),
):
    return await agent_memory.list_failed_agent_memory_jobs(user_id=user_id, db=db)


@router.get("/jobs/metrics")
async def get_job_metrics(
    user=Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_session),
):
    return await get_agent_memory_job_metrics(db=db)


@router.post("/jobs/failed/retry")
async def retry_failed_jobs(
    form_data: RetryFailedJobsForm,
    user=Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_session),
):
    return await agent_memory.retry_failed_agent_memory_jobs(user_id=form_data.user_id, db=db)


@router.post("/extract/run")
async def run_extraction_once(
    request: Request,
    form_data: RunJobsForm | None = None,
    user=Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_session),
):
    completed = await run_agent_memory_extraction_jobs_once(
        request,
        limit=form_data.limit if form_data else None,
        db=db,
    )
    return {"completed": completed}


@router.post("/consolidate/run")
async def run_consolidation_once(
    request: Request,
    form_data: RunJobsForm | None = None,
    user=Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_session),
):
    completed = await run_agent_memory_consolidation_jobs_once(
        request,
        limit=form_data.limit if form_data else None,
        db=db,
    )
    return {"completed": completed}


@router.post("/index/rebuild")
async def rebuild_index(
    request: Request,
    form_data: RebuildIndexForm,
    user=Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_session),
):
    return await agent_memory.rebuild_agent_memory_index(
        request,
        user_id=form_data.user_id,
        scope_type=form_data.scope_type,
        scope_id=form_data.scope_id,
        db=db,
    )


async def _reset_memory(form_data: ResetAgentMemoryForm, db: AsyncSession):
    return await agent_memory.clear_agent_memory(
        user_id=form_data.user_id,
        note_mode=form_data.note_mode,
        scope_type=form_data.scope_type,
        scope_id=form_data.scope_id,
        db=db,
    )


@router.post("/reset")
async def reset_memory(
    form_data: ResetAgentMemoryForm,
    user=Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_session),
):
    return await _reset_memory(form_data, db)


@router.post("/clear")
async def clear_memory(
    form_data: ClearAgentMemoryForm,
    user=Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_session),
):
    return await _reset_memory(form_data, db)
