import hashlib
import logging
import time
from typing import Any, Optional

from open_webui.internal.db import Base, JSONField, get_async_db_context
from open_webui.models.access_grants import AccessGrantModel, AccessGrants
from open_webui.models.groups import Groups
from open_webui.models.users import User, UserModel, UserResponse, Users
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    delete,
    func,
    or_,
    select,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

log = logging.getLogger(__name__)

####################
# Skills DB Schema
####################


class Skill(Base):
    __tablename__ = 'skill'

    id = Column(String, primary_key=True, unique=True)
    user_id = Column(String)
    name = Column(Text, unique=True)
    description = Column(Text, nullable=True)
    content = Column(Text)
    meta = Column(JSON)
    is_active = Column(Boolean, default=True)

    updated_at = Column(BigInteger)
    created_at = Column(BigInteger)


class SkillPackage(Base):
    __tablename__ = 'skill_package'

    id = Column(String, primary_key=True, unique=True)
    skill_id = Column(String, ForeignKey('skill.id', ondelete='CASCADE'), nullable=False)
    bundle_hash = Column(String, nullable=False)
    manifest = Column(JSONField, nullable=False)
    storage_path = Column(Text, nullable=False)

    updated_at = Column(BigInteger, nullable=False)
    created_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        UniqueConstraint('skill_id', 'bundle_hash', name='uq_skill_package_skill_bundle'),
        Index('ix_skill_package_skill_id', 'skill_id'),
        Index('ix_skill_package_bundle_hash', 'bundle_hash'),
    )


class SkillMeta(BaseModel):
    tags: Optional[list[str]] = []


class SkillPackageModel(BaseModel):
    id: str
    skill_id: str
    bundle_hash: str
    manifest: dict[str, Any]
    storage_path: str

    updated_at: int
    created_at: int

    model_config = ConfigDict(from_attributes=True)


class SkillModel(BaseModel):
    id: str
    user_id: str
    name: str
    description: Optional[str] = None
    content: str
    meta: SkillMeta
    is_active: bool = True
    access_grants: list[AccessGrantModel] = Field(default_factory=list)

    updated_at: int  # timestamp in epoch
    created_at: int  # timestamp in epoch

    model_config = ConfigDict(from_attributes=True)


####################
# Forms
####################


class SkillUserModel(SkillModel):
    user: Optional[UserResponse] = None


class SkillResponse(BaseModel):
    id: str
    user_id: str
    name: str
    description: Optional[str] = None
    meta: SkillMeta
    is_active: bool = True
    access_grants: list[AccessGrantModel] = Field(default_factory=list)
    updated_at: int  # timestamp in epoch
    created_at: int  # timestamp in epoch


class SkillUserResponse(SkillResponse):
    user: Optional[UserResponse] = None

    model_config = ConfigDict(extra='allow')


class SkillAccessResponse(SkillUserResponse):
    write_access: Optional[bool] = False


class SkillForm(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    content: str
    meta: SkillMeta = SkillMeta()
    is_active: bool = True
    access_grants: Optional[list[dict]] = None


class SkillListResponse(BaseModel):
    items: list[SkillUserResponse] = []
    total: int = 0


class SkillAccessListResponse(BaseModel):
    items: list[SkillAccessResponse] = []
    total: int = 0


class SkillsTable:
    async def _get_access_grants(self, skill_id: str, db: Optional[AsyncSession] = None) -> list[AccessGrantModel]:
        return await AccessGrants.get_grants_by_resource('skill', skill_id, db=db)

    async def _to_skill_model(
        self,
        skill: Skill,
        access_grants: Optional[list[AccessGrantModel]] = None,
        db: Optional[AsyncSession] = None,
    ) -> SkillModel:
        skill_data = SkillModel.model_validate(skill).model_dump(exclude={'access_grants'})
        skill_data['access_grants'] = (
            access_grants if access_grants is not None else await self._get_access_grants(skill_data['id'], db=db)
        )
        return SkillModel.model_validate(skill_data)

    async def insert_new_skill(
        self,
        user_id: str,
        form_data: SkillForm,
        db: Optional[AsyncSession] = None,
    ) -> Optional[SkillModel]:
        async with get_async_db_context(db) as db:
            try:
                result = Skill(
                    **{
                        **form_data.model_dump(exclude={'access_grants'}),
                        'user_id': user_id,
                        'updated_at': int(time.time()),
                        'created_at': int(time.time()),
                    }
                )
                db.add(result)
                await db.commit()
                await db.refresh(result)
                await AccessGrants.set_access_grants('skill', result.id, form_data.access_grants, db=db)
                if result:
                    return await self._to_skill_model(result, db=db)
                else:
                    return None
            except Exception as e:
                log.exception(f'Error creating a new skill: {e}')
                return None

    async def get_skill_by_id(self, id: str, db: Optional[AsyncSession] = None) -> Optional[SkillModel]:
        try:
            async with get_async_db_context(db) as db:
                skill = await db.get(Skill, id)
                return await self._to_skill_model(skill, db=db) if skill else None
        except Exception:
            return None

    async def get_skill_by_name(self, name: str, db: Optional[AsyncSession] = None) -> Optional[SkillModel]:
        try:
            async with get_async_db_context(db) as db:
                result = await db.execute(select(Skill).filter_by(name=name))
                skill = result.scalars().first()
                return await self._to_skill_model(skill, db=db) if skill else None
        except Exception:
            return None

    async def get_skills(self, db: Optional[AsyncSession] = None) -> list[SkillUserModel]:
        async with get_async_db_context(db) as db:
            result = await db.execute(select(Skill).order_by(Skill.updated_at.desc()))
            all_skills = result.scalars().all()

            user_ids = list(set(skill.user_id for skill in all_skills))
            skill_ids = [skill.id for skill in all_skills]

            users = await Users.get_users_by_user_ids(user_ids, db=db) if user_ids else []
            users_dict = {user.id: user for user in users}
            grants_map = await AccessGrants.get_grants_by_resources('skill', skill_ids, db=db)

            skills = []
            for skill in all_skills:
                user = users_dict.get(skill.user_id)
                skills.append(
                    SkillUserModel.model_validate(
                        {
                            **(
                                await self._to_skill_model(
                                    skill,
                                    access_grants=grants_map.get(skill.id, []),
                                    db=db,
                                )
                            ).model_dump(),
                            'user': user.model_dump() if user else None,
                        }
                    )
                )
            return skills

    async def get_skills_by_user_id(
        self, user_id: str, permission: str = 'write', db: Optional[AsyncSession] = None
    ) -> list[SkillUserModel]:
        skills = await self.get_skills(db=db)
        user_groups = await Groups.get_groups_by_member_id(user_id, db=db)
        user_group_ids = {group.id for group in user_groups}

        result = []
        for skill in skills:
            if skill.user_id == user_id:
                result.append(skill)
            elif await AccessGrants.has_access(
                user_id=user_id,
                resource_type='skill',
                resource_id=skill.id,
                permission=permission,
                user_group_ids=user_group_ids,
                db=db,
            ):
                result.append(skill)
        return result

    async def search_skills(
        self,
        user_id: str,
        filter: dict = {},
        skip: int = 0,
        limit: int = 30,
        db: Optional[AsyncSession] = None,
    ) -> SkillListResponse:
        try:
            async with get_async_db_context(db) as db:
                # Join with User table for user filtering
                stmt = select(Skill, User).outerjoin(User, User.id == Skill.user_id)

                if filter:
                    query_key = filter.get('query')
                    if query_key:
                        stmt = stmt.filter(
                            or_(
                                Skill.name.ilike(f'%{query_key}%'),
                                Skill.description.ilike(f'%{query_key}%'),
                                Skill.id.ilike(f'%{query_key}%'),
                                User.name.ilike(f'%{query_key}%'),
                                User.email.ilike(f'%{query_key}%'),
                            )
                        )

                    view_option = filter.get('view_option')
                    if view_option == 'created':
                        stmt = stmt.filter(Skill.user_id == user_id)
                    elif view_option == 'shared':
                        stmt = stmt.filter(Skill.user_id != user_id)

                    # Apply access grant filtering
                    stmt = AccessGrants.has_permission_filter(
                        db=db,
                        query=stmt,
                        DocumentModel=Skill,
                        filter=filter,
                        resource_type='skill',
                        permission='read',
                    )

                stmt = stmt.order_by(Skill.updated_at.desc())

                # Count BEFORE pagination
                count_result = await db.execute(select(func.count()).select_from(stmt.subquery()))
                total = count_result.scalar()

                if skip:
                    stmt = stmt.offset(skip)
                if limit:
                    stmt = stmt.limit(limit)

                result = await db.execute(stmt)
                items = result.all()

                skill_ids = [skill.id for skill, _ in items]
                grants_map = await AccessGrants.get_grants_by_resources('skill', skill_ids, db=db)

                skills = []
                for skill, user in items:
                    skills.append(
                        SkillUserResponse(
                            **(
                                await self._to_skill_model(
                                    skill,
                                    access_grants=grants_map.get(skill.id, []),
                                    db=db,
                                )
                            ).model_dump(),
                            user=(UserResponse(**UserModel.model_validate(user).model_dump()) if user else None),
                        )
                    )

                return SkillListResponse(items=skills, total=total)
        except Exception as e:
            log.exception(f'Error searching skills: {e}')
            return SkillListResponse(items=[], total=0)

    async def update_skill_by_id(
        self, id: str, updated: dict, db: Optional[AsyncSession] = None
    ) -> Optional[SkillModel]:
        try:
            async with get_async_db_context(db) as db:
                access_grants = updated.pop('access_grants', None)
                await db.execute(update(Skill).filter_by(id=id).values(**updated, updated_at=int(time.time())))
                await db.commit()
                if access_grants is not None:
                    await AccessGrants.set_access_grants('skill', id, access_grants, db=db)

                skill = await db.get(Skill, id)
                await db.refresh(skill)
                return await self._to_skill_model(skill, db=db)
        except Exception:
            return None

    async def toggle_skill_by_id(self, id: str, db: Optional[AsyncSession] = None) -> Optional[SkillModel]:
        async with get_async_db_context(db) as db:
            try:
                result = await db.execute(select(Skill).filter_by(id=id))
                skill = result.scalars().first()
                if not skill:
                    return None

                skill.is_active = not skill.is_active
                skill.updated_at = int(time.time())
                await db.commit()
                await db.refresh(skill)

                return await self._to_skill_model(skill, db=db)
            except Exception:
                return None

    async def delete_skill_by_id(self, id: str, db: Optional[AsyncSession] = None) -> bool:
        try:
            async with get_async_db_context(db) as db:
                await AccessGrants.revoke_all_access('skill', id, db=db)
                await db.execute(delete(Skill).filter_by(id=id))
                await db.commit()

                return True
        except Exception:
            return False

    async def upsert_skill_package(
        self,
        skill_id: str,
        bundle_hash: str,
        manifest: dict[str, Any],
        storage_path: str,
        db: Optional[AsyncSession] = None,
    ) -> Optional[SkillPackageModel]:
        try:
            async with get_async_db_context(db) as db:
                package = await self._upsert_skill_package_row(
                    db,
                    skill_id=skill_id,
                    bundle_hash=bundle_hash,
                    manifest=manifest,
                    storage_path=storage_path,
                )
                try:
                    await db.commit()
                except IntegrityError:
                    await db.rollback()
                    package = await self._upsert_skill_package_row(
                        db,
                        skill_id=skill_id,
                        bundle_hash=bundle_hash,
                        manifest=manifest,
                        storage_path=storage_path,
                    )
                    await db.commit()
                await db.refresh(package)
                return SkillPackageModel.model_validate(package)
        except Exception as e:
            try:
                await db.rollback()
            except Exception:
                pass
            log.exception(f'Error upserting skill package: {e}')
            return None

    async def update_skill_and_upsert_package_by_id(
        self,
        id: str,
        updated: dict,
        *,
        bundle_hash: str,
        manifest: dict[str, Any],
        storage_path: str,
        db: Optional[AsyncSession] = None,
    ) -> Optional[SkillModel]:
        try:
            async with get_async_db_context(db) as db:
                try:
                    if not await self._update_skill_and_package_rows(
                        db,
                        id,
                        updated,
                        bundle_hash=bundle_hash,
                        manifest=manifest,
                        storage_path=storage_path,
                    ):
                        return None
                    await db.commit()
                except IntegrityError:
                    await db.rollback()
                    if not await self._update_skill_and_package_rows(
                        db,
                        id,
                        updated,
                        bundle_hash=bundle_hash,
                        manifest=manifest,
                        storage_path=storage_path,
                    ):
                        return None
                    await db.commit()

                skill = await db.get(Skill, id)
                await db.refresh(skill)
                return await self._to_skill_model(skill, db=db)
        except Exception as e:
            try:
                await db.rollback()
            except Exception:
                pass
            log.exception(f'Error updating skill and package: {e}')
            return None

    async def _update_skill_and_package_rows(
        self,
        db: AsyncSession,
        id: str,
        updated: dict,
        *,
        bundle_hash: str,
        manifest: dict[str, Any],
        storage_path: str,
    ) -> bool:
        result = await db.execute(update(Skill).filter_by(id=id).values(**updated, updated_at=int(time.time())))
        if result.rowcount == 0:
            await db.rollback()
            return False

        await self._upsert_skill_package_row(
            db,
            skill_id=id,
            bundle_hash=bundle_hash,
            manifest=manifest,
            storage_path=storage_path,
        )
        return True

    async def _upsert_skill_package_row(
        self,
        db: AsyncSession,
        *,
        skill_id: str,
        bundle_hash: str,
        manifest: dict[str, Any],
        storage_path: str,
    ) -> SkillPackage:
        now = _skill_package_timestamp()
        result = await db.execute(select(SkillPackage).filter_by(skill_id=skill_id, bundle_hash=bundle_hash))
        package = result.scalars().first()
        if package:
            package.manifest = manifest
            package.storage_path = storage_path
            package.updated_at = now
            return package

        package = SkillPackage(
            id=skill_package_id(skill_id, bundle_hash),
            skill_id=skill_id,
            bundle_hash=bundle_hash,
            manifest=manifest,
            storage_path=storage_path,
            created_at=now,
            updated_at=now,
        )
        db.add(package)
        return package

    async def get_latest_skill_package_by_skill_id(
        self,
        skill_id: str,
        db: Optional[AsyncSession] = None,
    ) -> Optional[SkillPackageModel]:
        try:
            async with get_async_db_context(db) as db:
                result = await db.execute(
                    select(SkillPackage)
                    .filter_by(skill_id=skill_id)
                    .order_by(SkillPackage.updated_at.desc(), SkillPackage.created_at.desc(), SkillPackage.id.desc())
                    .limit(1)
                )
                package = result.scalars().first()
                return SkillPackageModel.model_validate(package) if package else None
        except Exception:
            return None


Skills = SkillsTable()


def skill_package_id(skill_id: str, bundle_hash: str) -> str:
    skill_id_digest = hashlib.sha256(skill_id.encode('utf-8')).hexdigest()
    return f'skillpkg_{skill_id_digest}_{bundle_hash}'


def _skill_package_timestamp() -> int:
    return time.time_ns()
