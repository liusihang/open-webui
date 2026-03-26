from typing import Optional

from sqlalchemy.orm import Session

from open_webui.models.knowledge_layers import KnowledgeLayers
from open_webui.retrieval.vector.factory import VECTOR_DB_CLIENT

LAYER_EMBEDDINGS_COLLECTION = "knowledge-layers"


def layer_vector_id(layer_row_id: str) -> str:
    return f"knowledge-layer:{layer_row_id}"


def delete_layer_vectors_by_file(file_id: str) -> None:
    VECTOR_DB_CLIENT.delete(
        collection_name=LAYER_EMBEDDINGS_COLLECTION,
        filter={"file_id": file_id},
    )


def delete_layer_vectors_by_knowledge(knowledge_id: str) -> None:
    VECTOR_DB_CLIENT.delete(
        collection_name=LAYER_EMBEDDINGS_COLLECTION,
        filter={"knowledge_id": knowledge_id},
    )


def delete_layer_embeddings_by_row_id(layer_row_id: str) -> None:
    VECTOR_DB_CLIENT.delete(
        collection_name=LAYER_EMBEDDINGS_COLLECTION,
        ids=[layer_vector_id(layer_row_id)],
    )


def delete_layer_embeddings_by_file_id(file_id: str) -> None:
    delete_layer_vectors_by_file(file_id)


def delete_layer_embeddings_by_knowledge_id(knowledge_id: str) -> None:
    delete_layer_vectors_by_knowledge(knowledge_id)


async def sync_file_layer_embeddings(
    request,
    knowledge_id: str,
    file_id: str,
    db: Optional[Session] = None,
):
    rows = [
        row
        for row in KnowledgeLayers.get_layers_by_file(knowledge_id, file_id, db=db)
        if row.status == "ready" and (row.content or "").strip()
    ]

    if not rows:
        return 0

    items = []
    failed = False
    for row in rows:
        try:
            KnowledgeLayers.mark_embedding_indexing(row.id, db=db)
            vector = await request.app.state.EMBEDDING_FUNCTION(row.content or "")
            items.append(
                {
                    "id": layer_vector_id(row.id),
                    "text": row.content or "",
                    "vector": vector,
                    "metadata": {
                        "knowledge_id": row.knowledge_id,
                        "file_id": row.file_id,
                        "layer_type": row.layer_type,
                        "part_index": row.part_index,
                        "part_total": row.part_total,
                        "layer_row_id": row.id,
                    },
                }
            )
        except Exception as exc:
            KnowledgeLayers.mark_embedding_failed(row.id, str(exc), db=db)
            failed = True

    if failed or not items:
        return 0

    delete_layer_vectors_by_file(file_id)
    VECTOR_DB_CLIENT.upsert(
        collection_name=LAYER_EMBEDDINGS_COLLECTION,
        items=items,
    )
    for row in rows:
        if any(item["metadata"]["layer_row_id"] == row.id for item in items):
            KnowledgeLayers.mark_embedding_ready(row.id, db=db)
    return len(items)
