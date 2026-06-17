import logging
import mimetypes
import os
import shutil
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse
from open_webui.config import UPLOAD_DIR
from open_webui.constants import ERROR_MESSAGES
from open_webui.internal.db import get_async_session
from open_webui.models.chats import Chats
from open_webui.models.folders import (
    FolderForm,
    FolderModel,
    FolderNameIdResponse,
    Folders,
    FolderUpdateForm,
)
from open_webui.utils import agent_memory
from open_webui.utils.access_control import has_permission
from open_webui.utils.access_control.files import get_accessible_folder_files
from open_webui.utils.auth import get_admin_user, get_verified_user
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)


router = APIRouter()


def _is_agent_memory_disabled(meta: dict | None) -> bool:
    agent_memory_meta = (meta or {}).get('agent_memory') or {}
    return bool(agent_memory_meta.get('disabled'))


def _merge_agent_memory_disabled_meta(meta: dict | None, old_meta: dict | None) -> dict | None:
    if not meta or not isinstance(meta.get('agent_memory'), dict) or 'disabled' not in meta['agent_memory']:
        return meta

    next_meta = dict(meta)
    next_agent_memory = dict((old_meta or {}).get('agent_memory') or {})
    next_agent_memory.update(next_meta.get('agent_memory') or {})
    if next_agent_memory.get('disabled'):
        next_agent_memory['disabled'] = True
    else:
        next_agent_memory.pop('disabled', None)

    if next_agent_memory:
        next_meta['agent_memory'] = next_agent_memory
    else:
        next_meta.pop('agent_memory', None)
    return next_meta


############################
# Get Folders
############################


@router.get('/', response_model=list[FolderNameIdResponse])
async def get_folders(
    request: Request,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    if request.app.state.config.ENABLE_FOLDERS is False:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    if user.role != 'admin' and not await has_permission(
        user.id,
        'features.folders',
        request.app.state.config.USER_PERMISSIONS,
        db=db,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    folders = await Folders.get_folders_by_user_id(user.id, db=db)

    # Verify folder data integrity
    folder_list = []
    for folder in folders:
        if folder.parent_id and not await Folders.get_folder_by_id_and_user_id(folder.parent_id, user.id, db=db):
            folder = await Folders.update_folder_parent_id_by_id_and_user_id(folder.id, user.id, None, db=db)

        if folder.data and 'files' in folder.data:
            accessible_files = await get_accessible_folder_files(folder.data['files'], user, db=db)
            if len(accessible_files) != len(folder.data.get('files', [])):
                folder.data['files'] = accessible_files
                await Folders.update_folder_by_id_and_user_id(
                    folder.id, user.id, FolderUpdateForm(data=folder.data), db=db
                )

        folder_list.append(FolderNameIdResponse(**folder.model_dump()))

    return folder_list


############################
# Create Folder
############################


@router.post('/')
async def create_folder(
    form_data: FolderForm,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    folder = await Folders.get_folder_by_parent_id_and_user_id_and_name(
        form_data.parent_id, user.id, form_data.name, db=db
    )

    if folder:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT('Folder already exists'),
        )

    try:
        folder = await Folders.insert_new_folder(user.id, form_data, form_data.parent_id, db=db)
        return folder
    except Exception as e:
        log.exception(e)
        log.error('Error creating folder')
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT('Error creating folder'),
        )


############################
# Get Folders By Id
############################


@router.get('/{id}', response_model=Optional[FolderModel])
async def get_folder_by_id(id: str, user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)):
    folder = await Folders.get_folder_by_id_and_user_id(id, user.id, db=db)
    if folder:
        return folder
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


############################
# Update Folder Name By Id
############################


@router.post('/{id}/update')
async def update_folder_name_by_id(
    id: str,
    form_data: FolderUpdateForm,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    folder = await Folders.get_folder_by_id_and_user_id(id, user.id, db=db)
    if folder:
        was_agent_memory_disabled = _is_agent_memory_disabled(folder.meta)
        if form_data.meta is not None:
            form_data.meta = _merge_agent_memory_disabled_meta(form_data.meta, folder.meta)

        if form_data.name is not None:
            # Check if folder with same name exists
            existing_folder = await Folders.get_folder_by_parent_id_and_user_id_and_name(
                folder.parent_id, user.id, form_data.name, db=db
            )
            if existing_folder and existing_folder.id != id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=ERROR_MESSAGES.DEFAULT('Folder already exists'),
                )

        # Validate read access to every file/collection being attached.
        # Folder files are consumed by chat middleware as RAG context.
        if form_data.data and isinstance(form_data.data.get('files'), list):
            accessible_files = await get_accessible_folder_files(form_data.data['files'], user, db=db)
            if len(accessible_files) != len(form_data.data['files']):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
                )

        try:
            folder = await Folders.update_folder_by_id_and_user_id(id, user.id, form_data, db=db)
            if folder and not was_agent_memory_disabled and _is_agent_memory_disabled(folder.meta):
                await agent_memory.set_folder_agent_memory_disabled(
                    user_id=user.id,
                    folder_id=id,
                    disabled=True,
                    db=db,
                )
                folder = await Folders.get_folder_by_id_and_user_id(id, user.id, db=db)
            return folder
        except Exception as e:
            log.exception(e)
            log.error(f'Error updating folder: {id}')
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT('Error updating folder'),
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


############################
# Update Folder Parent Id By Id
############################


class FolderParentIdForm(BaseModel):
    parent_id: Optional[str] = None


@router.post('/{id}/update/parent')
async def update_folder_parent_id_by_id(
    id: str,
    form_data: FolderParentIdForm,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    folder = await Folders.get_folder_by_id_and_user_id(id, user.id, db=db)
    if folder:
        existing_folder = await Folders.get_folder_by_parent_id_and_user_id_and_name(
            form_data.parent_id, user.id, folder.name, db=db
        )

        if existing_folder:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT('Folder already exists'),
            )

        try:
            folder = await Folders.update_folder_parent_id_by_id_and_user_id(id, user.id, form_data.parent_id, db=db)
            return folder
        except Exception as e:
            log.exception(e)
            log.error(f'Error updating folder: {id}')
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT('Error updating folder'),
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


############################
# Update Folder Is Expanded By Id
############################


class FolderIsExpandedForm(BaseModel):
    is_expanded: bool


@router.post('/{id}/update/expanded')
async def update_folder_is_expanded_by_id(
    id: str,
    form_data: FolderIsExpandedForm,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    folder = await Folders.get_folder_by_id_and_user_id(id, user.id, db=db)
    if folder:
        try:
            folder = await Folders.update_folder_is_expanded_by_id_and_user_id(
                id, user.id, form_data.is_expanded, db=db
            )
            return folder
        except Exception as e:
            log.exception(e)
            log.error(f'Error updating folder: {id}')
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT('Error updating folder'),
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


############################
# Delete Folder By Id
############################


@router.delete('/{id}')
async def delete_folder_by_id(
    request: Request,
    id: str,
    delete_contents: Optional[bool] = True,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    root_folder = await Folders.get_folder_by_id_and_user_id(id, user.id, db=db)
    if not root_folder:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    child_folders = await Folders.get_children_folders_by_id_and_user_id(id, user.id, db=db) or []
    affected_folder_ids = list(dict.fromkeys(folder.id for folder in [root_folder, *child_folders] if folder))
    chat_ids_by_folder = {
        folder_id: await agent_memory.list_chat_ids_in_folder(user.id, folder_id, db=db)
        for folder_id in affected_folder_ids
    }

    if any(chat_ids_by_folder.values()):
        chat_delete_permission = await has_permission(
            user.id, 'chat.delete', request.app.state.config.USER_PERMISSIONS, db=db
        )
        if user.role != 'admin' and not chat_delete_permission:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
            )

    try:
        for folder_id in affected_folder_ids:
            if delete_contents:
                if not await Chats.delete_chats_by_user_id_and_folder_id(user.id, folder_id, db=db):
                    raise RuntimeError(f'Failed to delete chats for folder {folder_id}')
                for chat_id in chat_ids_by_folder.get(folder_id, []):
                    await agent_memory.forget_chat_agent_memory(
                        user_id=user.id,
                        chat_id=chat_id,
                        folder_id=folder_id,
                        db=db,
                    )
                await agent_memory.remove_agent_memory_scope_outputs(
                    user_id=user.id,
                    scope_type='folder',
                    scope_id=folder_id,
                    db=db,
                )
            else:
                if not await Chats.move_chats_by_user_id_and_folder_id(user.id, folder_id, None, db=db):
                    raise RuntimeError(f'Failed to move chats out of folder {folder_id}')
                await agent_memory.remove_agent_memory_scope_outputs(
                    user_id=user.id,
                    scope_type='folder',
                    scope_id=folder_id,
                    db=db,
                )
                if chat_ids_by_folder.get(folder_id, []):
                    await agent_memory.enqueue_consolidation_for_scope(
                        user_id=user.id,
                        scope_type='global',
                        scope_id='',
                        db=db,
                    )

        folder_ids = await Folders.delete_folder_by_id_and_user_id(id, user.id, db=db)
        if not folder_ids:
            raise RuntimeError(f'Failed to delete folder {id}')
        return True
    except HTTPException:
        raise
    except Exception as e:
        log.exception(e)
        log.error(f'Error deleting folder: {id}')
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT('Error deleting folder'),
        )
