"""Read OOXML embedded media and real relationships without rendering Office.

DOCX has paragraph/part anchors, PPTX has presentation-order slides, and XLSX
has worksheet/cell anchors. None of these is a PDF page number.
"""

from __future__ import annotations

import posixpath
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from open_webui.retrieval.document_image_assets import (
    ImageAssetExtraction,
    ImageAssetStore,
    safe_relative_reference,
)

MAX_ZIP_ENTRIES = 3000
MAX_XML_BYTES = 2 * 1024 * 1024
MAX_ZIP_BYTES = 100 * 1024 * 1024


def _local(tag: str) -> str:
    return tag.rsplit('}', 1)[-1]


def _attribute(element, name):
    # Presentation sldId has both an unqualified numeric id and r:id.
    return next(
        (value for key, value in element.attrib.items() if _local(key) == name and (name != 'id' or '}' in key)), None
    )


def _xml(archive, name):
    info = archive.getinfo(name)
    if info.file_size > MAX_XML_BYTES:
        raise ValueError('office_xml_too_large')
    data = archive.read(info)
    if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        raise ValueError('office_xml_entities_forbidden')
    return ET.fromstring(data)


def _relationships(archive, owner: str) -> dict:
    parent, name = posixpath.split(owner)
    relation_path = posixpath.join(parent, '_rels', name + '.rels')
    if relation_path not in archive.namelist():
        return {}
    result = {}
    for relation in _xml(archive, relation_path):
        target = relation.get('Target', '')
        if relation.get('TargetMode') == 'External':
            continue  # Never retrieve linked pictures or external data.
        if target.startswith('//') or '\\' in target or ':' in target:
            raise ValueError('unsafe_office_relationship')
        # OOXML absolute targets are package-root URIs, not filesystem paths.
        resolved = posixpath.normpath(target.lstrip('/') if target.startswith('/') else posixpath.join(parent, target))
        safe_relative_reference(resolved)
        result[relation.get('Id')] = resolved
    return result


def _slide_order(archive) -> dict[str, int]:
    if 'ppt/presentation.xml' not in archive.namelist():
        return {}
    relations = _relationships(archive, 'ppt/presentation.xml')
    slides = [element for element in _xml(archive, 'ppt/presentation.xml').iter() if _local(element.tag) == 'sldId']
    return {
        relations[_attribute(slide, 'id')]: index
        for index, slide in enumerate(slides)
        if _attribute(slide, 'id') in relations
    }


def _sheet_drawings(archive) -> dict[str, str]:
    if 'xl/workbook.xml' not in archive.namelist():
        return {}
    sheets = {}
    relations = _relationships(archive, 'xl/workbook.xml')
    for element in _xml(archive, 'xl/workbook.xml').iter():
        if _local(element.tag) != 'sheet':
            continue
        sheet_path = relations.get(_attribute(element, 'id'))
        if not sheet_path:
            continue
        for target in _relationships(archive, sheet_path).values():
            if target.startswith('xl/drawings/'):
                sheets[target] = element.get('name', sheet_path)
    return sheets


def _cell_anchor(ancestors):
    cell = next((child for node in ancestors for child in node if _local(child.tag) == 'from'), None)
    result = {}
    if cell is not None:
        for name in ('row', 'col'):
            value = next((child.text for child in cell if _local(child.tag) == name), None)
            if value is not None and value.isdecimal():
                result[name + '_index'] = int(value)
    return result


def _image_context(element, *, owner, target, relation_id, parents, paragraphs, slides, drawings):
    anchor = {'part': owner, 'media_part': target, 'relationship_id': relation_id}
    ancestors, current = [element], element
    while current in parents:
        current = parents[current]
        ancestors.append(current)
    paragraph = next((node for node in ancestors if node in paragraphs), None)
    if paragraph is not None:
        anchor['paragraph_index'] = paragraphs[paragraph]
    if owner in slides:
        anchor['slide_index'] = slides[owner]
    if owner in drawings:
        anchor['sheet_name'] = drawings[owner]
        anchor.update(_cell_anchor(ancestors))
    local_context = paragraph if paragraph is not None else ancestors[-1]
    text = ' '.join(node.text or '' for node in local_context.iter() if _local(node.tag) == 't')
    graphic = next((node for node in ancestors if _local(node.tag) in ('drawing', 'pic')), element)
    descriptions = [
        node.get('descr') or node.get('title') for node in graphic.iter() if _local(node.tag) in ('docPr', 'cNvPr')
    ]
    caption = next(filter(None, descriptions), '')
    return anchor, caption, text


def _extract_part(archive, owner, *, store, slides, drawings, result):
    relations = _relationships(archive, owner)
    if not any('/media/' in target for target in relations.values()):
        return
    root = _xml(archive, owner)
    parents = {child: parent for parent in root.iter() for child in parent}
    paragraphs = {
        element: index for index, element in enumerate(element for element in root.iter() if _local(element.tag) == 'p')
    }
    for element in root.iter():
        relation_id = _attribute(element, 'id' if _local(element.tag) == 'imagedata' else 'embed')
        target = relations.get(relation_id)
        if not target or '/media/' not in target:
            continue
        anchor, caption, text = _image_context(
            element,
            owner=owner,
            target=target,
            relation_id=relation_id,
            parents=parents,
            paragraphs=paragraphs,
            slides=slides,
            drawings=drawings,
        )
        try:
            info = archive.getinfo(target)
            if info.file_size > store.max_image_bytes:
                raise ValueError('image_size_exceeded')
            result.assets.append(
                store.put(
                    archive.read(info),
                    backend='office_ooxml',
                    origin_reference=target,
                    anchor=anchor,
                    caption=caption,
                    surrounding_text=text,
                )
            )
        except Exception:
            result.skipped.append({'reason': 'office_image_unreadable', 'anchor': anchor})
        if store.count >= store.max_images:
            return


def extract_office_image_assets(
    file_path: str | Path, *, asset_root: str | Path, source_id: str | None = None, document_text: str | None = None
) -> ImageAssetExtraction:
    result = ImageAssetExtraction()
    store = ImageAssetStore(asset_root, source_id or str(Path(file_path).resolve()))
    try:
        with zipfile.ZipFile(file_path) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ZIP_ENTRIES or sum(info.file_size for info in entries) > MAX_ZIP_BYTES:
                raise ValueError('office_archive_budget_exceeded')
            names = set(archive.namelist())
            for name in names:
                safe_relative_reference(name)
            if len(names) != len(entries):
                raise ValueError('office_duplicate_archive_entries')
            slides = _slide_order(archive)
            drawings = _sheet_drawings(archive)
            owners = sorted(
                name
                for name in names
                if name.endswith('.xml') and name.startswith(('word/', 'ppt/', 'xl/')) and '/_rels/' not in name
            )
            for owner in owners:
                try:
                    _extract_part(archive, owner, store=store, slides=slides, drawings=drawings, result=result)
                    if store.count >= store.max_images:
                        result.skipped.append({'reason': 'document_image_budget_exceeded'})
                        return result
                except Exception:
                    result.skipped.append({'reason': 'office_part_unreadable', 'anchor': {'part': owner}})
    except Exception as exc:
        reason = str(exc) if isinstance(exc, ValueError) and str(exc).isidentifier() else 'office_archive_unreadable'
        result.skipped.append({'reason': reason})
    return result
