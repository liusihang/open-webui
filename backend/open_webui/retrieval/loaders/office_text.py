"""Lightweight OOXML text fallback; no rendering, formula evaluation or network.

Keep one document per presentation-order slide / workbook-order worksheet.
The image loader reads the same package relationships separately.
"""

import zipfile
from xml.etree import ElementTree as ET

from langchain_core.documents import Document
from open_webui.retrieval.document_image_assets import safe_relative_reference
from open_webui.retrieval.loaders.office_image_assets import (
    MAX_ZIP_BYTES,
    MAX_ZIP_ENTRIES,
    _attribute,
    _local,
    _relationships,
    _slide_order,
)


class _NoDoctype(ET.TreeBuilder):
    def doctype(self, name, pubid, system):
        raise ValueError('office_xml_entities_forbidden')


def _text_xml(archive, name):
    # A sheet/shared-string table can exceed the image parser's 2 MiB XML
    # limit. The whole package is already bounded before any XML is read.
    return ET.fromstring(archive.read(name), parser=ET.XMLParser(target=_NoDoctype()))


def _string_text(element):
    # Rich text runs are concatenated; phonetic annotations are not content.
    parts = []
    for child in element:
        if _local(child.tag) == 't':
            parts.append(child.text or '')
        elif _local(child.tag) == 'r':
            parts.extend(node.text or '' for node in child if _local(node.tag) == 't')
    return ''.join(parts)


def _cell_text(cell, strings):
    children = {_local(node.tag): node for node in cell}
    kind = cell.get('t')
    if kind == 'inlineStr':
        return _string_text(children['is']) if 'is' in children else ''
    value = children['v'].text if 'v' in children else None
    if value is not None:
        if kind == 's':
            index = int(value)
            if index < 0 or index >= len(strings):
                raise ValueError('office_shared_string_index_invalid')
            return strings[index]
        if kind == 'b':
            return 'TRUE' if value == '1' else 'FALSE'
        return value
    # Formulas are never executed. Prefer their saved cached value; when no
    # cache exists, retain the formula rather than silently losing the cell.
    formula = children.get('f')
    return '=' + formula.text if formula is not None and formula.text else ''


def _sheet_lines(root, strings):
    lines = []
    for row in root.iter():
        if _local(row.tag) != 'row':
            continue
        values = []
        for cell in row:
            if _local(cell.tag) == 'c':
                text = _cell_text(cell, strings)
                if text:
                    values.append(f'{cell.get("r", "")}: {text}')
        if values:
            lines.append('\t'.join(values))
    return lines


class OfficeTextLoader:
    def __init__(self, file_path, file_ext):
        self.file_path = str(file_path)
        self.file_ext = file_ext

    def load(self) -> list[Document]:
        with zipfile.ZipFile(self.file_path) as archive:
            entries = archive.infolist()
            names = set(archive.namelist())
            if len(entries) > MAX_ZIP_ENTRIES or sum(info.file_size for info in entries) > MAX_ZIP_BYTES:
                raise ValueError('office_archive_budget_exceeded')
            if len(names) != len(entries):
                raise ValueError('office_duplicate_archive_entries')
            for name in names:
                safe_relative_reference(name)
            return self._slides(archive) if self.file_ext == 'pptx' else self._sheets(archive)

    def _document(self, content, anchor):
        return Document(page_content=content, metadata={'source': self.file_path, 'anchor': anchor})

    def _slides(self, archive):
        archive.getinfo('ppt/presentation.xml')  # Reject a mislabeled/non-PPTX package.
        docs = []
        for part, index in _slide_order(archive).items():
            root = _text_xml(archive, part)
            paragraphs = []
            for paragraph in root.iter():
                if _local(paragraph.tag) != 'p':
                    continue
                parts = []
                for node in paragraph.iter():
                    kind = _local(node.tag)
                    if kind == 't':
                        parts.append(node.text or '')
                    elif kind in ('br', 'tab'):
                        parts.append('\n' if kind == 'br' else '\t')
                text = ''.join(parts).strip()
                if text:
                    paragraphs.append(text)
            if paragraphs:
                docs.append(self._document('\n'.join(paragraphs), {'part': part, 'slide_index': index}))
        return docs

    def _sheets(self, archive):
        strings = []
        if 'xl/sharedStrings.xml' in archive.namelist():
            strings = [_string_text(node) for node in _text_xml(archive, 'xl/sharedStrings.xml')]
        relations = _relationships(archive, 'xl/workbook.xml')
        docs = []
        for sheet in _text_xml(archive, 'xl/workbook.xml').iter():
            if _local(sheet.tag) != 'sheet':
                continue
            part = relations.get(_attribute(sheet, 'id'))
            if not part or not part.startswith('xl/worksheets/'):
                continue  # Chartsheets contain no cell grid.
            lines = _sheet_lines(_text_xml(archive, part), strings)
            if lines:
                docs.append(self._document('\n'.join(lines), {'part': part, 'sheet_name': sheet.get('name', part)}))
        return docs
