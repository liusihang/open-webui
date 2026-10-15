"""Sanitized replay of the accepted PaddleOCR-VL-1.6 jobs page shape.

The live response uses HTML img/div markup and a neighboring figure title,
not markdown alt-text. Keep that source title without inventing a caption.
No real network or credential is used by this regression.
"""

import base64
import io
import json
from pathlib import Path
from unittest.mock import Mock

from open_webui.retrieval.loaders.paddleocr_vl import PaddleOCRVLLoader
from PIL import Image


def test_jobs_html_figure_retains_bitmap_page_and_existing_title(monkeypatch, tmp_path):
    def blocked(*args, **kwargs):
        raise AssertionError('Unexpected real network')

    monkeypatch.setattr('requests.sessions.Session.request', blocked)
    source = tmp_path / 'synthetic-chart.pdf'
    source.write_bytes(b'%PDF-offline-mocked-input')
    picture = io.BytesIO()
    Image.new('RGB', (84, 50), '#2463eb').save(picture, 'JPEG')
    encoded = base64.b64encode(picture.getvalue()).decode()
    caption = 'Figure 1. Blue, red and green bars in ascending height.'
    reference = 'imgs/img_in_chart_box_190_431_1039_994.jpg'
    text = ('## Synthetic Figure Extraction Test\n\n'
            f'<div><img src="{reference}" alt="Image" /></div>\n\n'
            f'<div style="text-align: center;">{caption}</div>')
    payload = {'logId': 'sanitized-fixture', 'errorCode': 0, 'errorMsg': 'Success',
               'result': {'layoutParsingResults': [{
                   'prunedResult': {'page_count': 1, 'parsing_res_list': [
                       {'block_label': 'chart', 'block_content': '', 'block_bbox': [190, 431, 1039, 994]},
                       {'block_label': 'figure_title', 'block_content': caption,
                        'block_bbox': [104, 1033, 698, 1067]}]},
                   'markdown': {'text': text, 'images': {reference: encoded}},
                   'outputImages': {'layout_res': encoded}, 'inputImage': encoded}]}}
    download = Mock(return_value=json.dumps(payload).encode())
    monkeypatch.setattr('open_webui.retrieval.loaders.paddleocr_vl.download_service_resource', download)
    loader = PaddleOCRVLLoader('https://ocr.example/api/v2/ocr/jobs', 'fixture-only-token',
        str(source), api_mode='jobs', extract_images=True, asset_root=tmp_path / 'assets')
    rows = loader._download_job_results('https://ocr.example/result.jsonl')
    docs = loader._build_documents(rows, resource_base_url='https://ocr.example/result.jsonl')
    assert len(docs) == 1 and docs[0].page_content == text
    assert docs[0].metadata['page'] == 0 and docs[0].metadata['page_label'] == 1
    assets = docs[0].metadata['document_image_assets']
    assert len(assets) == 1  # not outputImages or inputImage debug bitmaps
    asset = assets[0]
    assert (asset['width'], asset['height'], asset['page_index']) == (84, 50, 0)
    assert asset['caption'] == ''  # no specific markdown alt caption was supplied
    assert caption in asset['surrounding_text']
    assert Path(asset['storage_uri']).read_bytes() == picture.getvalue()
    assert docs[0].metadata['document_image_assets_skipped'] == []
