"""Add multimodal evidence schema

Revision ID: d1e2f3a4b5c6
Revises: c7d8e9f0a1b2
Create Date: 2026-06-07 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from open_webui.internal.db import JSONField

revision: str = "d1e2f3a4b5c6"
down_revision: Union[str, None] = "c7d8e9f0a1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


ASSET_TABLE = "knowledge_evidence_asset"
ASSET_VARIANT_TABLE = "knowledge_evidence_asset_variant"
EVIDENCE_TABLE = "knowledge_evidence"
VECTOR_SPACE_TABLE = "knowledge_vector_space"
EMBEDDING_TABLE = "knowledge_evidence_embedding"

TABLE_INDEXES = {
    "ix_knowledge_evidence_asset_knowledge_id": (ASSET_TABLE, ["knowledge_id"]),
    "ix_knowledge_evidence_asset_file_id": (ASSET_TABLE, ["file_id"]),
    "ix_knowledge_evidence_asset_status": (ASSET_TABLE, ["status"]),
    "ix_knowledge_evidence_asset_variant_asset_id": (ASSET_VARIANT_TABLE, ["asset_id"]),
    "ix_knowledge_evidence_asset_variant_variant_kind": (ASSET_VARIANT_TABLE, ["variant_kind"]),
    "ix_knowledge_evidence_knowledge_id": (EVIDENCE_TABLE, ["knowledge_id"]),
    "ix_knowledge_evidence_file_id": (EVIDENCE_TABLE, ["file_id"]),
    "ix_knowledge_evidence_asset_id": (EVIDENCE_TABLE, ["asset_id"]),
    "ix_knowledge_evidence_retrieval_chunk_uid": (EVIDENCE_TABLE, ["retrieval_chunk_uid"]),
    "ix_knowledge_evidence_retrieval_chunk_row_id": (EVIDENCE_TABLE, ["retrieval_chunk_row_id"]),
    "ix_knowledge_vector_space_knowledge_id": (VECTOR_SPACE_TABLE, ["knowledge_id"]),
    "ix_knowledge_vector_space_active": (VECTOR_SPACE_TABLE, ["active"]),
    "ix_knowledge_evidence_embedding_evidence_id": (EMBEDDING_TABLE, ["evidence_id"]),
    "ix_knowledge_evidence_embedding_evidence_ref": (EMBEDDING_TABLE, ["evidence_ref"]),
    "ix_knowledge_evidence_embedding_vector_space_id": (EMBEDDING_TABLE, ["vector_space_id"]),
    "ix_knowledge_evidence_embedding_status": (EMBEDDING_TABLE, ["embedding_status"]),
}


def _table_exists(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def _existing_index_names(inspector: sa.Inspector, table_name: str) -> set[str]:
    if not _table_exists(inspector, table_name):
        return set()
    return {index["name"] for index in inspector.get_indexes(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _table_exists(inspector, ASSET_TABLE):
        _create_asset_table()
    if not _table_exists(inspector, ASSET_VARIANT_TABLE):
        _create_asset_variant_table()
    if not _table_exists(inspector, EVIDENCE_TABLE):
        _create_evidence_table()
    if not _table_exists(inspector, VECTOR_SPACE_TABLE):
        _create_vector_space_table()
    if not _table_exists(inspector, EMBEDDING_TABLE):
        _create_embedding_table()

    inspector = sa.inspect(bind)
    for index_name, (table_name, columns) in TABLE_INDEXES.items():
        if index_name not in _existing_index_names(inspector, table_name):
            op.create_index(index_name, table_name, columns)


def _create_asset_table() -> None:
    op.create_table(
        ASSET_TABLE,
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("knowledge_id", sa.Text(), sa.ForeignKey("knowledge.id", ondelete="CASCADE"), nullable=False),
        sa.Column("file_id", sa.Text(), sa.ForeignKey("file.id", ondelete="CASCADE"), nullable=False),
        sa.Column("asset_ref", sa.Text(), nullable=False),
        sa.Column("asset_kind", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.Text(), nullable=False),
        sa.Column("storage_uri", sa.Text(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("page_index", sa.Integer(), nullable=True),
        sa.Column("bbox_json", JSONField(), nullable=True),
        sa.Column("anchor_json", JSONField(), nullable=True),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column("ocr_text", sa.Text(), nullable=True),
        sa.Column("surrounding_text", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_knowledge_evidence_asset"),
        sa.UniqueConstraint("asset_ref", name="uq_knowledge_evidence_asset_asset_ref"),
    )


def _create_asset_variant_table() -> None:
    op.create_table(
        ASSET_VARIANT_TABLE,
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column(
            "asset_id",
            sa.Text(),
            sa.ForeignKey("knowledge_evidence_asset.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("variant_kind", sa.Text(), nullable=False),
        sa.Column("storage_uri", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.Text(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("transform_config_hash", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_knowledge_evidence_asset_variant"),
        sa.UniqueConstraint(
            "asset_id",
            "variant_kind",
            "transform_config_hash",
            name="uq_knowledge_evidence_asset_variant_identity",
        ),
    )


def _create_evidence_table() -> None:
    op.create_table(
        EVIDENCE_TABLE,
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("evidence_ref", sa.Text(), nullable=False),
        sa.Column("knowledge_id", sa.Text(), sa.ForeignKey("knowledge.id", ondelete="CASCADE"), nullable=False),
        sa.Column("file_id", sa.Text(), sa.ForeignKey("file.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "asset_id",
            sa.Text(),
            sa.ForeignKey("knowledge_evidence_asset.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("retrieval_chunk_uid", sa.Text(), nullable=True),
        sa.Column("retrieval_chunk_row_id", sa.Integer(), nullable=True),
        sa.Column("modality", sa.Text(), nullable=False),
        sa.Column("evidence_kind", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("content_text", sa.Text(), nullable=True),
        sa.Column("preview_text", sa.Text(), nullable=True),
        sa.Column("source_name", sa.Text(), nullable=False),
        sa.Column("page_index", sa.Integer(), nullable=True),
        sa.Column("anchor_json", JSONField(), nullable=True),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("chunk_total", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("projection_profile", sa.Text(), nullable=False),
        sa.Column("projection_config_hash", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("deleted_at", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_knowledge_evidence"),
        sa.UniqueConstraint("evidence_ref", name="uq_knowledge_evidence_evidence_ref"),
    )


def _create_vector_space_table() -> None:
    op.create_table(
        VECTOR_SPACE_TABLE,
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("knowledge_id", sa.Text(), sa.ForeignKey("knowledge.id", ondelete="CASCADE"), nullable=False),
        sa.Column("retrieval_profile", sa.Text(), nullable=False),
        sa.Column("embedding_model", sa.Text(), nullable=False),
        sa.Column("embedding_dim", sa.Integer(), nullable=True),
        sa.Column("distance_metric", sa.Text(), nullable=False),
        sa.Column("vector_backend", sa.Text(), nullable=False),
        sa.Column("supports_text_query", sa.Boolean(), nullable=False),
        sa.Column("supports_image_query", sa.Boolean(), nullable=False),
        sa.Column("supports_text_evidence", sa.Boolean(), nullable=False),
        sa.Column("supports_image_evidence", sa.Boolean(), nullable=False),
        sa.Column("supports_multivector", sa.Boolean(), nullable=False),
        sa.Column("projection_config_hash", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_knowledge_vector_space"),
        sa.UniqueConstraint(
            "knowledge_id",
            "retrieval_profile",
            "projection_config_hash",
            name="uq_knowledge_vector_space_identity",
        ),
    )


def _create_embedding_table() -> None:
    op.create_table(
        EMBEDDING_TABLE,
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column(
            "evidence_id",
            sa.Text(),
            sa.ForeignKey("knowledge_evidence.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("evidence_ref", sa.Text(), nullable=False),
        sa.Column(
            "vector_space_id",
            sa.Text(),
            sa.ForeignKey("knowledge_vector_space.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("vector_backend_collection", sa.Text(), nullable=False),
        sa.Column("vector_backend_id", sa.Text(), nullable=True),
        sa.Column("vector_role", sa.Text(), nullable=False),
        sa.Column("embedding_format", sa.Text(), nullable=False),
        sa.Column("embedding_status", sa.Text(), nullable=False),
        sa.Column("embedding_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_knowledge_evidence_embedding"),
        sa.UniqueConstraint(
            "evidence_id",
            "vector_space_id",
            "vector_role",
            "vector_backend_collection",
            name="uq_knowledge_evidence_embedding_identity",
        ),
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    for index_name, (table_name, _columns) in reversed(list(TABLE_INDEXES.items())):
        if index_name in _existing_index_names(inspector, table_name):
            op.drop_index(index_name, table_name=table_name)

    inspector = sa.inspect(bind)
    for table_name in [EMBEDDING_TABLE, VECTOR_SPACE_TABLE, EVIDENCE_TABLE, ASSET_VARIANT_TABLE, ASSET_TABLE]:
        if _table_exists(inspector, table_name):
            op.drop_table(table_name)
