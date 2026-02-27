import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from open_webui.utils.auth import get_admin_user


log = logging.getLogger(__name__)
router = APIRouter()


class RebuildToolSearchForm(BaseModel):
    scope: Literal["all", "mcp", "local"] = "all"


@router.get("/status")
async def get_tool_search_status(request: Request, user=Depends(get_admin_user)):
    service = getattr(request.app.state, "TOOL_SEARCH_SERVICE", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Tool search service is not available",
        )

    return await service.get_status()


@router.post("/rebuild")
async def rebuild_tool_search(
    request: Request,
    form_data: RebuildToolSearchForm,
    user=Depends(get_admin_user),
):
    service = getattr(request.app.state, "TOOL_SEARCH_SERVICE", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Tool search service is not available",
        )

    try:
        return await service.rebuild(scope=form_data.scope)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        log.exception(f"Failed to rebuild tool search index: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to rebuild tool search index",
        )


@router.post("/rebuild/mcp")
async def rebuild_tool_search_mcp(request: Request, user=Depends(get_admin_user)):
    service = getattr(request.app.state, "TOOL_SEARCH_SERVICE", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Tool search service is not available",
        )

    try:
        return await service.rebuild(scope="mcp")
    except Exception as e:
        log.exception(f"Failed to rebuild MCP tool search index: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to rebuild MCP tool search index",
        )
