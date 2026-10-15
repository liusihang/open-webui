"""Explicit local PDF embedded-image extraction. No OCR or page rendering."""

from pathlib import Path

from open_webui.retrieval.document_image_assets import ImageAssetExtraction, ImageAssetStore


def extract_pdf_image_assets(
    file_path: str | Path,
    *,
    asset_root: str | Path,
    source_id: str | None = None,
    page_text_by_index: dict[int, str] | None = None,
) -> ImageAssetExtraction:
    result = ImageAssetExtraction()
    store = ImageAssetStore(asset_root, source_id or str(Path(file_path).resolve()))
    try:
        from pypdf import PdfReader

        reader = PdfReader(file_path)
        for index, page in enumerate(reader.pages):
            if index >= 200:
                result.skipped.append({'reason': 'pdf_page_limit_exceeded', 'page_index': index})
                break
            try:
                for ordinal, image in enumerate(page.images):
                    if store.count >= store.max_images:
                        result.skipped.append({'reason': 'document_image_budget_exceeded', 'page_index': index})
                        return result
                    try:
                        result.assets.append(
                            store.put(
                                image.data,
                                backend='pypdf',
                                page_index=index,
                                origin_reference=f'page/{index}/image/{ordinal}',
                                surrounding_text=(page_text_by_index or {}).get(index, ''),
                            )
                        )
                    except Exception:
                        result.skipped.append(
                            {'reason': 'pdf_image_unreadable', 'page_index': index, 'ordinal': ordinal}
                        )
            except Exception:
                result.skipped.append({'reason': 'pdf_page_images_unavailable', 'page_index': index})
    except Exception:
        result.skipped.append({'reason': 'pdf_read_failed'})
    return result
