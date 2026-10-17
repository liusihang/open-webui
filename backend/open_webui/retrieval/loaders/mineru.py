import base64
import json
import logging
import os
import re
import tempfile
import time
import zipfile
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import List, Optional

import requests
from fastapi import HTTPException, status
from langchain_core.documents import Document
from open_webui.retrieval.document_image_assets import (
    ImageAssetExtraction,
    ImageAssetStore,
    extract_markdown_image_assets,
    safe_relative_reference,
)

log = logging.getLogger(__name__)

# The V1 API embeds every image as a base64 data URI, which is not text worth indexing
MARKDOWN_IMAGE = re.compile(r'!\[([^\[\]\n]*)\]\(data:[^()\s]*\)')
HTML_IMAGE = re.compile(r'<img\b[^<>]*>', re.IGNORECASE)


class MinerULoader:
    """
    MinerU document parser loader supporting both Cloud API and Local API modes.

    Cloud API: Uses MinerU managed service with async task-based processing
    Local API: Uses self-hosted MinerU API with synchronous processing
    """

    def __init__(
        self,
        file_path: str,
        api_mode: str = 'local',
        api_url: str = 'http://localhost:8000',
        api_key: str = '',
        params: dict = None,
        timeout: Optional[int] = 300,
        max_markdown_bytes: Optional[int] = None,
        extract_images: bool = False,
        asset_root: str | Path | None = None,
        source_id: str | None = None,
    ):
        self.file_path = file_path
        self.api_mode = api_mode.lower()
        self.api_url = api_url.rstrip('/')
        self.api_key = api_key
        self.timeout = timeout
        self.max_markdown_bytes = max_markdown_bytes
        self.extract_images = extract_images
        self.asset_root = asset_root
        self.source_id = source_id or str(Path(file_path).resolve())
        self.image_extraction = ImageAssetExtraction()
        if extract_images and asset_root is None:
            raise ValueError('MinerU image extraction requires a private asset root')

        # Parse params dict with defaults
        self.params = dict(params or {})
        self.enable_ocr = self.params.get('enable_ocr', False)
        self.enable_formula = self.params.get('enable_formula', True)
        self.enable_table = self.params.get('enable_table', True)
        self.language = self.params.get('language', 'en')
        self.model_version = self.params.get('model_version', 'pipeline')
        self.tier = self.params.get('tier')

        self.page_ranges = self.params.pop('page_ranges', '')

        # Validate API mode
        if self.api_mode not in ['local', 'cloud']:
            raise ValueError(f"Invalid API mode: {self.api_mode}. Must be 'local' or 'cloud'")

        # Validate Cloud API requirements
        if self.api_mode == 'cloud' and not self.api_key:
            raise ValueError('API key is required for Cloud API mode')

    def load(self) -> List[Document]:
        """
        Main entry point for loading and parsing the document.
        Routes to Cloud or Local API based on api_mode.
        """
        try:
            self.image_extraction = ImageAssetExtraction()
            if self.api_mode == 'cloud':
                return self._load_cloud_api()
            elif self._is_v1_api():
                return self._load_v1_api()
            else:
                return self._load_local_api()
        except Exception as e:
            log.error(f'Error loading document with MinerU: {e}')
            raise

    def _load_local_api(self) -> List[Document]:
        """
        Load document using Local API (synchronous).
        Posts file to /file_parse endpoint and gets immediate response.
        """
        log.info('Using MinerU Local API at %s', self.api_url)

        filename = os.path.basename(self.file_path)

        # Build form data for Local API
        form_data = {
            **self.params,
            'return_md': 'true',
            **({'return_images': 'true', 'return_content_list': 'true'} if self.extract_images else {}),
        }

        # Page ranges (Local API uses start_page_id and end_page_id)
        if self.page_ranges:
            # For simplicity, if page_ranges is specified, log a warning
            # Full page range parsing would require parsing the string
            log.warning(
                f"Page ranges '{self.page_ranges}' specified but Local API uses different format. "
                'Consider using start_page_id/end_page_id parameters if needed.'
            )

        try:
            with open(self.file_path, 'rb') as f:
                files = {'files': (filename, f, 'application/octet-stream')}

                log.info('Sending file to MinerU Local API: %s', filename)
                log.debug('Local API parameters: %s', form_data)

                response = requests.post(
                    f'{self.api_url}/file_parse',
                    data=form_data,
                    files=files,
                    timeout=self.timeout,
                )
                response.raise_for_status()

        except FileNotFoundError:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f'File not found: {self.file_path}')
        except requests.Timeout:
            raise HTTPException(
                status.HTTP_504_GATEWAY_TIMEOUT,
                detail='MinerU Local API request timed out',
            )
        except requests.HTTPError as e:
            error_detail = f'MinerU Local API request failed: {e}'
            if e.response is not None:
                try:
                    error_data = e.response.json()
                    error_detail += f' - {error_data}'
                except Exception:
                    error_detail += f' - {e.response.text}'
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=error_detail)
        except Exception as e:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f'Error calling MinerU Local API: {str(e)}',
            )

        # Parse response
        try:
            result = response.json()
        except ValueError as e:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                detail=f'Invalid JSON response from MinerU Local API: {e}',
            )

        # Extract markdown content from response
        if 'results' not in result:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                detail="MinerU Local API response missing 'results' field",
            )

        results = result['results']
        if not results:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail='MinerU returned empty results',
            )

        # Get the first (and typically only) result
        file_result = list(results.values())[0]
        markdown_content = file_result.get('md_content', '')

        if not markdown_content:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail='MinerU returned empty markdown content',
            )

        log.info('Successfully parsed document with MinerU Local API: %s', filename)

        # Create metadata
        metadata = {
            'source': filename,
            'api_mode': 'local',
            'backend': result.get('backend', 'unknown'),
            'version': result.get('version', 'unknown'),
        }

        if self.extract_images:
            self._materialize_images(file_result.get('images'), file_result.get('content_list'), markdown_content)
            metadata.update(self._asset_metadata())

        return [Document(page_content=markdown_content, metadata=metadata)]

    def _is_v1_api(self) -> bool:
        """
        Detect MinerU 4.x, whose V1 API replaced the /file_parse endpoint of 3.x.
        """
        try:
            return requests.get(f'{self.api_url}/v1/health', timeout=10).ok
        except requests.RequestException:
            return False

    def _load_v1_api(self) -> List[Document]:
        """
        Load document using the MinerU 4.x V1 API (asynchronous).
        Uploads the file, submits a parse job, polls it and downloads the markdown.
        """
        log.info('Using MinerU V1 API at %s', self.api_url)

        filename = os.path.basename(self.file_path)

        upload = self._request_v1_api(
            'POST',
            '/v1/uploads',
            json={
                'filename': filename,
                'bytes': os.path.getsize(self.file_path),
                'mime_type': 'application/octet-stream',
            },
        ).json()
        with open(self.file_path, 'rb') as f:
            self._request_v1_api('PUT', f'/v1/uploads/{upload["id"]}/content', data=f)
        upload = self._request_v1_api('POST', f'/v1/uploads/{upload["id"]}/complete').json()

        file_entry = {'source': {'type': 'file_id', 'file_id': upload['file']['id']}}
        if self.page_ranges and filename.lower().endswith('.pdf'):
            file_entry['page_range'] = self.page_ranges

        # The job rejects unknown fields, so the 3.x params are not passed through
        job = self._request_v1_api(
            'POST',
            '/v1/parse/jobs',
            json={
                'files': [file_entry],
                'tier': self.tier,
                'ocr_mode': 'ocr' if self.enable_ocr else 'auto',
            },
        ).json()

        log.info('Polling MinerU V1 job: %s', job['job_id'])
        deadline = time.monotonic() + self.timeout if self.timeout else None
        while job['status'] in ['queued', 'running']:
            if deadline and time.monotonic() > deadline:
                raise HTTPException(
                    status.HTTP_504_GATEWAY_TIMEOUT,
                    detail=f'MinerU processing timed out after {self.timeout} seconds',
                )
            time.sleep(2)
            job = self._request_v1_api('GET', f'/v1/parse/jobs/{job["job_id"]}').json()

        file_result = job['files'][0]
        if job['status'] != 'completed':
            error = file_result.get('error') or {}
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail=f'MinerU processing {job["status"]}: {error.get("message", "Unknown error")}',
            )

        markdown_id = file_result['output_files']['markdown']['file_id']
        markdown_content = self._request_v1_api('GET', f'/v1/files/{markdown_id}/content').content.decode('utf-8')
        if self.extract_images:
            self._materialize_v1_images(markdown_content)
        markdown_content = HTML_IMAGE.sub('', MARKDOWN_IMAGE.sub(r'\1', markdown_content))

        if not markdown_content and not (self.image_extraction.assets or self.image_extraction.skipped):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail='MinerU returned empty markdown content',
            )

        log.info('Successfully parsed document with MinerU V1 API: %s', filename)

        metadata = {
            'source': filename,
            'api_mode': 'local',
            'tier': job['tier'],
            'version': (file_result.get('parse') or {}).get('parser_version', 'unknown'),
        }

        if self.extract_images:
            metadata.update(self._asset_metadata())
            if not markdown_content:
                metadata['_metadata_only'] = True

        return [Document(page_content=markdown_content, metadata=metadata)]

    def _request_v1_api(self, method: str, path: str, **kwargs) -> requests.Response:
        """
        Send one request to the MinerU V1 API and raise an HTTPException on failure.
        """
        try:
            response = requests.request(method, f'{self.api_url}{path}', timeout=self.timeout, **kwargs)
            response.raise_for_status()
        except requests.Timeout:
            raise HTTPException(
                status.HTTP_504_GATEWAY_TIMEOUT,
                detail='MinerU V1 API request timed out',
            )
        except requests.HTTPError as e:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail=f'MinerU V1 API request failed: {e} - {e.response.text}',
            )
        except Exception as e:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f'Error calling MinerU V1 API: {str(e)}',
            )
        return response

    def _load_cloud_api(self) -> List[Document]:
        """
        Load document using Cloud API (asynchronous).
        Uses batch upload endpoint to avoid need for public file URLs.
        """
        log.info('Using MinerU Cloud API at %s', self.api_url)

        filename = os.path.basename(self.file_path)

        # Step 1: Request presigned upload URL
        batch_id, upload_url = self._request_upload_url(filename)

        # Step 2: Upload file to presigned URL
        self._upload_to_presigned_url(upload_url)

        # Step 3: Poll for results
        result = self._poll_batch_status(batch_id, filename)

        # Step 4: Download and extract markdown from ZIP
        markdown_content = self._download_and_extract_zip(result['full_zip_url'], filename)

        log.info('Successfully parsed document with MinerU Cloud API: %s', filename)

        # Create metadata
        metadata = {
            'source': filename,
            'api_mode': 'cloud',
            'batch_id': batch_id,
        }
        if self.extract_images:
            metadata.update(self._asset_metadata())

        return [Document(page_content=markdown_content, metadata=metadata)]

    def _request_upload_url(self, filename: str) -> tuple:
        """
        Request presigned upload URL from Cloud API.
        Returns (batch_id, upload_url).
        """
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json',
        }

        # Build request body
        request_body = {
            **self.params,
            'files': [
                {
                    'name': filename,
                    'is_ocr': self.enable_ocr,
                }
            ],
        }

        # Add page ranges if specified
        if self.page_ranges:
            request_body['files'][0]['page_ranges'] = self.page_ranges

        log.info('Requesting upload URL for: %s', filename)
        log.debug('Cloud API request body: %s', request_body)

        try:
            response = requests.post(
                f'{self.api_url}/file-urls/batch',
                headers=headers,
                json=request_body,
                timeout=30,
            )
            response.raise_for_status()
        except requests.HTTPError as e:
            error_detail = f'Failed to request upload URL: {e}'
            if e.response is not None:
                try:
                    error_data = e.response.json()
                    error_detail += f' - {error_data.get("msg", error_data)}'
                except Exception:
                    error_detail += f' - {e.response.text}'
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=error_detail)
        except Exception as e:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f'Error requesting upload URL: {str(e)}',
            )

        try:
            result = response.json()
        except ValueError as e:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                detail=f'Invalid JSON response: {e}',
            )

        # Check for API error response
        if result.get('code') != 0:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail=f'MinerU Cloud API error: {result.get("msg", "Unknown error")}',
            )

        data = result.get('data', {})
        batch_id = data.get('batch_id')
        file_urls = data.get('file_urls', [])

        if not batch_id or not file_urls:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                detail='MinerU Cloud API response missing batch_id or file_urls',
            )

        upload_url = file_urls[0]
        log.info('Received upload URL for batch: %s', batch_id)

        return batch_id, upload_url

    def _upload_to_presigned_url(self, upload_url: str) -> None:
        """
        Upload file to presigned URL (no authentication needed).
        """
        log.info('Uploading file to presigned URL')

        try:
            with open(self.file_path, 'rb') as f:
                response = requests.put(
                    upload_url,
                    data=f,
                    timeout=self.timeout,
                )
                response.raise_for_status()
        except FileNotFoundError:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f'File not found: {self.file_path}')
        except requests.Timeout:
            raise HTTPException(
                status.HTTP_504_GATEWAY_TIMEOUT,
                detail='File upload to presigned URL timed out',
            )
        except requests.HTTPError as e:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail=f'Failed to upload file to presigned URL: {e}',
            )
        except Exception as e:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f'Error uploading file: {str(e)}',
            )

        log.info('File uploaded successfully')

    def _poll_batch_status(self, batch_id: str, filename: str) -> dict:
        """
        Poll batch status until completion.
        Returns the result dict for the file.
        """
        headers = {
            'Authorization': f'Bearer {self.api_key}',
        }

        max_iterations = 300  # 10 minutes max (2 seconds per iteration)
        poll_interval = 2  # seconds

        log.info('Polling batch status: %s', batch_id)

        for iteration in range(max_iterations):
            try:
                response = requests.get(
                    f'{self.api_url}/extract-results/batch/{batch_id}',
                    headers=headers,
                    timeout=30,
                )
                response.raise_for_status()
            except requests.HTTPError as e:
                error_detail = f'Failed to poll batch status: {e}'
                if e.response is not None:
                    try:
                        error_data = e.response.json()
                        error_detail += f' - {error_data.get("msg", error_data)}'
                    except Exception:
                        error_detail += f' - {e.response.text}'
                raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=error_detail)
            except Exception as e:
                raise HTTPException(
                    status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f'Error polling batch status: {str(e)}',
                )

            try:
                result = response.json()
            except ValueError as e:
                raise HTTPException(
                    status.HTTP_502_BAD_GATEWAY,
                    detail=f'Invalid JSON response while polling: {e}',
                )

            # Check for API error response
            if result.get('code') != 0:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    detail=f'MinerU Cloud API error: {result.get("msg", "Unknown error")}',
                )

            data = result.get('data', {})
            extract_result = data.get('extract_result', [])

            # Find our file in the batch results
            file_result = None
            for item in extract_result:
                if item.get('file_name') == filename:
                    file_result = item
                    break

            if not file_result:
                raise HTTPException(
                    status.HTTP_502_BAD_GATEWAY,
                    detail=f'File {filename} not found in batch results',
                )

            state = file_result.get('state')

            if state == 'done':
                log.info('Processing complete for %s', filename)
                return file_result
            elif state == 'failed':
                error_msg = file_result.get('err_msg', 'Unknown error')
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    detail=f'MinerU processing failed: {error_msg}',
                )
            elif state in ['waiting-file', 'pending', 'running', 'converting']:
                # Still processing
                if iteration % 10 == 0:  # Log every 20 seconds
                    log.info('Processing status: %s (iteration %s/%s)', state, iteration + 1, max_iterations)
                time.sleep(poll_interval)
            else:
                log.warning(f'Unknown state: {state}')
                time.sleep(poll_interval)

        # Timeout
        raise HTTPException(
            status.HTTP_504_GATEWAY_TIMEOUT,
            detail='MinerU processing timed out after 10 minutes',
        )

    def _download_and_extract_zip(self, zip_url: str, filename: str) -> str:
        """
        Download ZIP file from CDN and extract markdown content.
        Returns the markdown content as a string.
        """
        log.info('Downloading MinerU result archive')

        try:
            response = requests.get(zip_url, timeout=60)
            response.raise_for_status()
        except requests.HTTPError as e:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail=f'Failed to download results ZIP: {e}',
            )
        except Exception as e:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f'Error downloading results: {str(e)}',
            )

        # Save ZIP to temporary file before reading.
        tmp_zip_path = None
        markdown_content = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix='.zip') as tmp_zip:
                tmp_zip.write(response.content)
                tmp_zip_path = tmp_zip.name

            with zipfile.ZipFile(tmp_zip_path, 'r') as zip_ref:
                members = zip_ref.infolist()
                all_files = [member.filename for member in members]
                md_members = [member for member in members if member.filename.endswith('.md')]
                read_errors = []

                for member in md_members:
                    log.info('Found markdown file in ZIP: %s', member.filename)
                    try:
                        with zip_ref.open(member, 'r') as f:
                            if self.max_markdown_bytes is None:
                                content = f.read()
                            else:
                                content = f.read(self.max_markdown_bytes + 1)
                                if len(content) > self.max_markdown_bytes:
                                    raise HTTPException(
                                        status.HTTP_502_BAD_GATEWAY,
                                        detail=f'Markdown file in results ZIP is too large: {member.filename}',
                                    )
                            markdown_content = content.decode('utf-8')
                    except UnicodeDecodeError as e:
                        read_errors.append(f'{member.filename}: {e}')
                        log.warning(f'Failed to decode {member.filename}: {e}')
                        continue
                    except HTTPException:
                        raise
                    except Exception as e:
                        read_errors.append(f'{member.filename}: {e}')
                        log.warning(f'Failed to read {member.filename}: {e}')
                        continue
                    if markdown_content:
                        break

                if markdown_content is None:
                    log.error(f'Available files in ZIP: {all_files}')
                    if read_errors:
                        error_msg = f"Found .md files but couldn't read them: {read_errors}"
                    else:
                        error_msg = f'No .md files found in ZIP. Available files: {all_files}'
                    raise HTTPException(
                        status.HTTP_502_BAD_GATEWAY,
                        detail=error_msg,
                    )
                if self.extract_images:
                    try:
                        self._materialize_zip_images(zip_ref, markdown_content)
                    except Exception:
                        self.image_extraction.skipped.append({'reason': 'mineru_archive_images_failed'})
        except zipfile.BadZipFile as e:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                detail=f'Invalid ZIP file received: {e}',
            )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f'Error extracting ZIP: {str(e)}',
            )
        finally:
            if tmp_zip_path:
                try:
                    os.unlink(tmp_zip_path)
                except FileNotFoundError:
                    pass
                except Exception as e:
                    log.warning(f'Failed to remove temporary ZIP file {tmp_zip_path}: {e}')

        if not markdown_content:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail='Extracted markdown content is empty',
            )

        log.info('Successfully extracted markdown content (%s characters)', len(markdown_content))
        return markdown_content

    def _asset_metadata(self):
        return {
            'document_image_assets': self.image_extraction.assets,
            'document_image_assets_skipped': self.image_extraction.skipped,
        }

    def _materialize_zip_images(self, archive, markdown):
        entries = archive.infolist()
        if len(entries) > 3000 or sum(entry.file_size for entry in entries) > 200 * 1024 * 1024:
            raise ValueError('mineru_archive_budget_exceeded')
        for entry in entries:
            safe_relative_reference(entry.filename)
        content_list = None
        for entry in entries:
            if entry.filename.endswith('_content_list.json') and entry.file_size <= 10 * 1024 * 1024:
                content_list = json.loads(archive.read(entry))
                break
        images = {}
        for entry in entries:
            if 'images' not in PurePosixPath(entry.filename).parts or entry.is_dir():
                continue
            if len(images) >= 200 or entry.file_size > 20 * 1024 * 1024:
                self.image_extraction.skipped.append({'reason': 'mineru_image_budget_exceeded'})
                continue
            # Keep the archive prefix; content-list matching also handles the
            # common images/name.jpg path without that enclosing directory.
            images[entry.filename] = base64.b64encode(archive.read(entry)).decode('ascii')
        self._materialize_images(images, content_list, markdown)

    def _materialize_v1_images(self, markdown):
        """Persist V1 inline images before removing their bytes from indexed text.

        Only explicit inline data is accepted; HTML/markdown URLs never trigger
        requests, and synthetic references keep credentials/base64 out of metadata.
        """
        images = {}
        rows = []

        def add(value, caption=''):
            if not value.startswith('data:image/'):
                return
            if len(images) >= 200:
                self.image_extraction.skipped.append({'reason': 'mineru_image_budget_exceeded'})
                return
            reference = f'images/v1-{len(images)}'
            images[reference] = value
            rows.append({'img_path': reference, 'image_caption': caption})

        for match in re.finditer(r'!\[([^\[\]\n]*)\]\((data:[^()\s]*)\)', markdown):
            add(match.group(2), match.group(1))

        class InlineImages(HTMLParser):
            def handle_starttag(self, tag, attrs):
                if tag.lower() == 'img':
                    attrs = dict(attrs)
                    add(attrs.get('src') or '', attrs.get('alt') or '')

        InlineImages().feed(markdown)
        # Surrounding text must not retain potentially large inline image bytes.
        clean_text = HTML_IMAGE.sub('', MARKDOWN_IMAGE.sub(r'\1', markdown))
        self._materialize_images(images, rows, clean_text)

    def _materialize_images(self, images, content_list, markdown):
        try:
            if isinstance(content_list, str):
                content_list = json.loads(content_list)
            rows = content_list if isinstance(content_list, list) else []
            store = ImageAssetStore(self.asset_root, self.source_id)
            for reference, value in images.items() if isinstance(images, dict) else []:
                matching = next(
                    (
                        row
                        for row in rows
                        if isinstance(row, dict)
                        and row.get('img_path')
                        and (reference == row['img_path'] or reference.endswith('/' + row['img_path']))
                    ),
                    {},
                )
                page_index = matching.get('page_idx')
                if type(page_index) is not int or page_index < 0:
                    page_index = None
                extraction = extract_markdown_image_assets(
                    {'text': markdown, 'images': {reference: value}},
                    store=store,
                    page_index=page_index,
                    backend='mineru',
                    resource_base_url=self.api_url,
                    # Local API returns inline base64; no extra remote origins.
                    allowed_origins=(),
                )
                captions = matching.get('image_caption') or []
                for asset in extraction.assets:
                    asset['anchor'] = {'parser_image_path': reference}
                    if isinstance(captions, list):
                        asset['caption'] = '\n'.join(str(item) for item in captions)[:4096]
                    elif isinstance(captions, str):
                        asset['caption'] = captions[:4096]
                self.image_extraction.assets.extend(extraction.assets)
                self.image_extraction.skipped.extend(extraction.skipped)
            if not images and ('![' in markdown or '<img' in markdown.lower()):
                self.image_extraction.skipped.append({'reason': 'parser_image_resources_missing'})
        except Exception:
            self.image_extraction.skipped.append({'reason': 'mineru_images_invalid'})
