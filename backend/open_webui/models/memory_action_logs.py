import json
import time
import uuid
from typing import Optional

from pydantic import BaseModel, ConfigDict
from sqlalchemy import BigInteger, Column, String, Text
from sqlalchemy.orm import Session

from open_webui.internal.db import Base, get_db_context


class MemoryActionLog(Base):
    __tablename__ = "memory_action_log"

    id = Column(String, primary_key=True, unique=True)
    user_id = Column(String, nullable=False)
    chat_id = Column(String, nullable=True)
    message_id = Column(String, nullable=True)
    status = Column(String, nullable=False)
    planner_model = Column(String, nullable=True)
    trigger_message = Column(Text, nullable=True)
    planned_actions = Column(Text, nullable=True)
    executed_actions = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(BigInteger, nullable=False)


class MemoryActionLogModel(BaseModel):
    id: str
    user_id: str
    chat_id: Optional[str] = None
    message_id: Optional[str] = None
    status: str
    planner_model: Optional[str] = None
    trigger_message: Optional[str] = None
    planned_actions: Optional[str] = None
    executed_actions: Optional[str] = None
    error: Optional[str] = None
    created_at: int

    model_config = ConfigDict(from_attributes=True)


class MemoryActionLogsTable:
    def insert_log(
        self,
        *,
        user_id: str,
        status: str,
        chat_id: Optional[str] = None,
        message_id: Optional[str] = None,
        planner_model: Optional[str] = None,
        trigger_message: Optional[str] = None,
        planned_actions: Optional[list[dict]] = None,
        executed_actions: Optional[list[dict]] = None,
        error: Optional[str] = None,
        db: Optional[Session] = None,
    ) -> Optional[MemoryActionLogModel]:
        with get_db_context(db) as db:
            log = MemoryActionLogModel(
                id=str(uuid.uuid4()),
                user_id=user_id,
                chat_id=chat_id,
                message_id=message_id,
                status=status,
                planner_model=planner_model,
                trigger_message=trigger_message,
                planned_actions=(
                    json.dumps(planned_actions, ensure_ascii=False)
                    if planned_actions is not None
                    else None
                ),
                executed_actions=(
                    json.dumps(executed_actions, ensure_ascii=False)
                    if executed_actions is not None
                    else None
                ),
                error=error,
                created_at=int(time.time()),
            )

            result = MemoryActionLog(**log.model_dump())
            db.add(result)
            db.commit()
            db.refresh(result)

            if result:
                return MemoryActionLogModel.model_validate(result)
            return None


MemoryActionLogs = MemoryActionLogsTable()
