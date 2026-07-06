from __future__ import annotations

import hashlib
import json
import re
import zipfile
from io import BytesIO
from pathlib import PurePosixPath
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field


class SkillPackageError(ValueError):
    pass


class ParsedSkillMarkdown(BaseModel):
    name: str | None = None
    description: str | None = None
    body: str


class ParsedSkillJson(BaseModel):
    entrypoints: list[dict[str, str]] = Field(default_factory=list)


class SkillPackageManifestFile(BaseModel):
    path: str
    sha256: str
    size: int


class SkillPackageManifest(BaseModel):
    schema_version: int = 1
    hash: str
    files: list[SkillPackageManifestFile]
    skill: ParsedSkillMarkdown
    entrypoints: list[dict[str, str]] = Field(default_factory=list)

    model_config = ConfigDict(frozen=True)


_STORAGE_SKILL_ID_UNSAFE_CHARS_RE = re.compile(r'[^A-Za-z0-9_.-]+')
_STORAGE_SKILL_ID_SLUG_MAX_LENGTH = 48
_SHA256_RE = re.compile(r'^[0-9a-f]{64}$')
_DISALLOWED_SKILL_JSON_FIELDS = {'id', 'name', 'description', 'version'}
_ALLOWED_ENTRYPOINT_FIELDS = {'name', 'path', 'runtime'}
_ALLOWED_TEXT_FILENAMES = {'SKILL.md', 'skill.json', 'README', 'README.md', 'LICENSE', 'NOTICE'}
_ALLOWED_TEXT_SUFFIXES = {
    '.cfg',
    '.css',
    '.csv',
    '.html',
    '.ini',
    '.jinja',
    '.js',
    '.json',
    '.jsx',
    '.md',
    '.py',
    '.sh',
    '.toml',
    '.ts',
    '.tsx',
    '.txt',
    '.yaml',
    '.yml',
}
MAX_SKILL_PACKAGE_FILES = 128
MAX_SKILL_PACKAGE_SINGLE_TEXT_BYTES = 256 * 1024
MAX_SKILL_PACKAGE_TOTAL_TEXT_BYTES = 2 * 1024 * 1024


def validate_package_file_path(path: str) -> str:
    if not isinstance(path, str):
        raise SkillPackageError('package file path must be a string')

    value = path
    if value != value.strip():
        raise SkillPackageError(f'package file path must not include leading or trailing whitespace: {path}')
    if not value or value == '.':
        raise SkillPackageError('package file path must not be empty')
    if '\x00' in value or '\\' in value:
        raise SkillPackageError(f'unsafe package file path: {path}')
    if value.startswith('/'):
        raise SkillPackageError(f'package file path must be relative: {path}')

    pure_path = PurePosixPath(value)
    if pure_path.is_absolute() or any(part in {'', '.', '..'} for part in pure_path.parts):
        raise SkillPackageError(f'unsafe package file path: {path}')
    if str(pure_path) != value:
        raise SkillPackageError(f'package file path must be normalized: {path}')

    return value


def parse_skill_markdown(content: str | bytes) -> ParsedSkillMarkdown:
    text = _decode_text(content, path='SKILL.md')
    text = _normalize_newlines(text)
    name = None
    description = None
    body = text

    if text.startswith('---\n'):
        lines = text.splitlines(keepends=True)
        closing_index = None
        for index, line in enumerate(lines[1:], start=1):
            if line.strip() == '---':
                closing_index = index
                break
        if closing_index is None:
            raise SkillPackageError('SKILL.md frontmatter is missing closing delimiter')

        frontmatter = ''.join(lines[1:closing_index])
        body = ''.join(lines[closing_index + 1 :])
        data = _parse_skill_markdown_frontmatter(frontmatter)
        name = data.get('name')
        description = data.get('description')

    return ParsedSkillMarkdown(name=name, description=description, body=body)


def parse_skill_json(content: str | bytes) -> ParsedSkillJson:
    text = _decode_text(content, path='skill.json')
    data = _load_skill_json_object(text)
    _validate_skill_json_fields(data)
    return ParsedSkillJson(entrypoints=_validate_entrypoints(data.get('entrypoints', [])))


def normalize_package_files(files: dict[str, str | bytes]) -> dict[str, bytes]:
    if len(files) > MAX_SKILL_PACKAGE_FILES:
        raise SkillPackageError(
            f'text-only package has too many text files: {len(files)} > max {MAX_SKILL_PACKAGE_FILES}'
        )

    normalized: dict[str, bytes] = {}
    total_size = 0
    for raw_path, content in files.items():
        path = validate_package_file_path(raw_path)
        if not is_supported_text_package_path(path):
            raise SkillPackageError(
                f'text-only package has unsupported file type: {path}; '
                'only UTF-8 text files with supported text extensions are allowed, '
                'and binary assets are not supported'
            )
        if path in normalized:
            raise SkillPackageError(f'duplicate package file path: {path}')

        text = _decode_text(content, path=path)
        text = _normalize_newlines(text)
        if '\x00' in text:
            raise SkillPackageError(f'text-only package file contains NUL bytes: {path}')
        encoded = text.encode('utf-8')
        size = len(encoded)
        if size > MAX_SKILL_PACKAGE_SINGLE_TEXT_BYTES:
            raise SkillPackageError(
                f'text-only package file exceeds max single text file size '
                f'({MAX_SKILL_PACKAGE_SINGLE_TEXT_BYTES} bytes): {path} ({size} bytes)'
            )
        total_size += size
        if total_size > MAX_SKILL_PACKAGE_TOTAL_TEXT_BYTES:
            raise SkillPackageError(
                f'text-only package exceeds max total text package size '
                f'({MAX_SKILL_PACKAGE_TOTAL_TEXT_BYTES} bytes) after adding {path}: {total_size} bytes'
            )
        normalized[path] = encoded

    return dict(sorted(normalized.items()))


def build_skill_package_manifest(files: dict[str, str | bytes]) -> SkillPackageManifest:
    normalized = normalize_package_files(files)
    if 'SKILL.md' not in normalized:
        raise SkillPackageError('skill package must include SKILL.md')

    skill = parse_skill_markdown(normalized['SKILL.md'])
    skill_json = parse_skill_json(normalized['skill.json']) if 'skill.json' in normalized else ParsedSkillJson()
    _validate_entrypoint_paths_are_packaged(skill_json.entrypoints, set(normalized))
    manifest_files = [
        SkillPackageManifestFile(path=path, sha256=_sha256_hex(content), size=len(content))
        for path, content in normalized.items()
    ]
    payload = {
        'schema_version': 1,
        'files': [file.model_dump() for file in manifest_files],
        'skill': {
            'name': skill.name,
            'description': skill.description,
        },
        'entrypoints': skill_json.entrypoints,
    }
    return SkillPackageManifest(
        hash=_sha256_hex(_canonical_json(payload).encode('utf-8')),
        files=manifest_files,
        skill=skill,
        entrypoints=skill_json.entrypoints,
    )


def build_skill_package_zip_bytes(files: dict[str, str | bytes]) -> bytes:
    normalized = normalize_package_files(files)
    output = BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for path, content in normalized.items():
            info = zipfile.ZipInfo(path)
            info.date_time = (1980, 1, 1, 0, 0, 0)
            info.create_system = 3
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content)
    return output.getvalue()


def skill_package_storage_filename(skill_id: str, bundle_hash: str) -> str:
    if not isinstance(skill_id, str) or not skill_id:
        raise SkillPackageError('skill id for package storage filename must be a non-empty string')
    if not isinstance(bundle_hash, str) or not _SHA256_RE.fullmatch(bundle_hash):
        raise SkillPackageError('bundle hash must be a lowercase sha256 hex digest')
    return f'skillpkg_{_skill_id_filename_token(skill_id)}_{bundle_hash}.zip'


def is_supported_text_package_path(path: str) -> bool:
    pure_path = PurePosixPath(path)
    return pure_path.name in _ALLOWED_TEXT_FILENAMES or pure_path.suffix.lower() in _ALLOWED_TEXT_SUFFIXES


def _parse_skill_markdown_frontmatter(frontmatter: str) -> dict[str, str]:
    try:
        data = yaml.safe_load(frontmatter)
    except yaml.YAMLError as exc:
        raise SkillPackageError(f'SKILL.md frontmatter is not valid YAML: {exc}') from exc

    if not isinstance(data, dict):
        raise SkillPackageError('SKILL.md frontmatter must be a YAML object')

    parsed: dict[str, str] = {}
    for field in ('name', 'description'):
        if field not in data or data[field] is None:
            continue
        if not isinstance(data[field], str):
            raise SkillPackageError(f'SKILL.md frontmatter field {field} must be a string')
        parsed[field] = data[field].strip()
    return parsed


def _skill_id_filename_token(skill_id: str) -> str:
    digest = hashlib.sha256(skill_id.encode('utf-8')).hexdigest()
    slug = _STORAGE_SKILL_ID_UNSAFE_CHARS_RE.sub('-', skill_id).strip('._-')
    if len(slug) > _STORAGE_SKILL_ID_SLUG_MAX_LENGTH:
        slug = slug[:_STORAGE_SKILL_ID_SLUG_MAX_LENGTH].strip('._-')
    if not slug:
        slug = 'skill'
    return f'{slug}-{digest}'


def _load_skill_json_object(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text or '{}')
    except json.JSONDecodeError as exc:
        raise SkillPackageError(f'skill.json is not valid JSON: {exc.msg}') from exc

    if not isinstance(data, dict):
        raise SkillPackageError('skill.json must be a JSON object')
    return data


def _validate_skill_json_fields(data: dict[str, Any]) -> None:
    disallowed = _DISALLOWED_SKILL_JSON_FIELDS.intersection(data)
    if disallowed:
        fields = ', '.join(sorted(disallowed))
        raise SkillPackageError(f'skill.json must not include descriptive fields: {fields}')

    extra_fields = set(data).difference({'entrypoints'})
    if extra_fields:
        fields = ', '.join(sorted(extra_fields))
        raise SkillPackageError(f'skill.json contains unsupported fields: {fields}')


def _validate_entrypoints(entrypoints: Any) -> list[dict[str, str]]:
    if not isinstance(entrypoints, list):
        raise SkillPackageError('skill.json entrypoints must be a list')

    return [_validate_entrypoint(index, entrypoint) for index, entrypoint in enumerate(entrypoints)]


def _validate_entrypoint(index: int, entrypoint: Any) -> dict[str, str]:
    if not isinstance(entrypoint, dict):
        raise SkillPackageError(f'skill.json entrypoint #{index + 1} must be an object')

    extra_fields = set(entrypoint).difference(_ALLOWED_ENTRYPOINT_FIELDS)
    if extra_fields:
        fields = ', '.join(sorted(extra_fields))
        raise SkillPackageError(f'skill.json entrypoint #{index + 1} contains unsupported fields: {fields}')

    missing_fields = _ALLOWED_ENTRYPOINT_FIELDS.difference(entrypoint)
    if missing_fields:
        fields = ', '.join(sorted(missing_fields))
        raise SkillPackageError(f'skill.json entrypoint #{index + 1} is missing required fields: {fields}')

    name = _validate_entrypoint_text(entrypoint['name'], field='name', index=index)
    path = validate_package_file_path(entrypoint['path'])
    runtime = _validate_entrypoint_text(entrypoint['runtime'], field='runtime', index=index)

    return {'name': name, 'path': path, 'runtime': runtime}


def _validate_entrypoint_text(value: Any, *, field: str, index: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SkillPackageError(f'skill.json entrypoint #{index + 1} {field} must be a non-empty string')
    return value.strip()


def _validate_entrypoint_paths_are_packaged(entrypoints: list[dict[str, str]], packaged_paths: set[str]) -> None:
    for entrypoint in entrypoints:
        path = entrypoint['path']
        if path not in packaged_paths:
            raise SkillPackageError(
                f"skill.json entrypoint {entrypoint['name']} path must point to a packaged file: {path}"
            )


def _decode_text(content: str | bytes, *, path: str) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, bytes):
        try:
            return content.decode('utf-8')
        except UnicodeDecodeError as exc:
            raise SkillPackageError(
                f'text-only package files must be UTF-8: {path}; binary assets are not supported'
            ) from exc
    raise SkillPackageError(f'text-only package file content must be text or bytes: {path}')


def _normalize_newlines(text: str) -> str:
    return text.replace('\r\n', '\n').replace('\r', '\n')


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), sort_keys=True)


def _sha256_hex(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
