import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("WEBUI_SECRET_KEY", "test-secret")
os.environ.setdefault("ENABLE_DB_MIGRATIONS", "false")

from open_webui.retrieval.loaders import paddleocr_vl
from open_webui.retrieval.loaders.paddleocr_vl import PaddleOCRVLLoader


def test_paddleocr_loader_projects_markdown_images_into_document_image_assets(tmp_path, monkeypatch):
    source_pdf = tmp_path / "source.pdf"
    source_pdf.write_bytes(b"%PDF-1.4\n")
    extracted_image = tmp_path / "box-a.png"
    extracted_image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 24)

    def fake_post(url, json, headers):
        assert url == "http://paddle.test/layout-parsing"
        assert json["fileType"] == 0
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {
                "result": {
                    "layoutParsingResults": [
                        {
                            "pageNo": 3,
                            "markdown": {
                                "text": "样品应放入 Box A，如下图所示。\n图 3.1 Box A 容器示意图。",
                                "images": {str(extracted_image): str(extracted_image)},
                            },
                        },
                        {
                            "pageNo": 4,
                            "markdown": {
                                "text": "",
                                "images": {str(extracted_image): str(extracted_image)},
                            },
                        },
                    ]
                }
            },
        )

    monkeypatch.setattr(paddleocr_vl.requests, "post", fake_post)
    monkeypatch.setattr(paddleocr_vl, "UPLOAD_DIR", tmp_path / "uploads", raising=False)

    docs = PaddleOCRVLLoader(
        api_url="http://paddle.test",
        token="token",
        file_path=str(source_pdf),
    ).load()

    assert len(docs) == 2
    assert docs[0].page_content.startswith("样品应放入 Box A")
    assert docs[0].metadata["page"] == 2
    assets = docs[0].metadata["document_image_assets"]
    assert len(assets) == 1
    assert assets[0]["storage_path"] == str(extracted_image.resolve())
    assert assets[0]["asset_kind"] == "document_image"
    assert assets[0]["page_index"] == 3
    assert assets[0]["caption"] == "样品应放入 Box A，如下图所示。"
    assert assets[0]["surrounding_text"] == "样品应放入 Box A，如下图所示。 图 3.1 Box A 容器示意图。"
    assert assets[0]["anchor"] == {"page": 3, "block_id": "page-003-image-001"}
    assert assets[0]["metadata"]["origin_reference"] == str(extracted_image)
    assert docs[1].page_content == ""
    assert docs[1].metadata["_metadata_only"] is True
    assert docs[1].metadata["document_image_assets"][0]["page_index"] == 4
