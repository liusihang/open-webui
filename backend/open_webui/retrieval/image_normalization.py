"""Bounded local image conversion for retrieval; original uploads are untouched."""

import gzip
import io
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from xml.etree import ElementTree as ET

from PIL import Image, ImageOps, UnidentifiedImageError

from open_webui.retrieval.image_errors import ImageProcessingError, format_hint

NATIVE_FORMATS = {
    'PNG': ('png', 'image/png'),
    'JPEG': ('jpg', 'image/jpeg'),
    'WEBP': ('webp', 'image/webp'),
    'GIF': ('gif', 'image/gif'),
    'TIFF': ('tiff', 'image/tiff'),
    'BMP': ('bmp', 'image/bmp'),
}
RASTER_FORMATS = {'AVIF', 'HEIF', 'ICO', 'ICNS', 'JPEG2000', 'MPO', 'PCX', 'PPM', 'PSD', 'TGA', 'DIB', 'DDS'}
VECTOR_SIZE = 2048


def _fail(code, kind, message):
    raise ImageProcessingError(code, kind, f'{kind} 图片{message}')


def _run(command, kind, *, data=None):
    executable = shutil.which(command[0])
    if not executable:
        _fail('image_converter_unavailable', kind, f'暂时无法转换：缺少 {command[0]} 转换器。')
    try:
        result = subprocess.run(
            [executable, *command[1:]],
            input=data,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=False,
        )
    except subprocess.TimeoutExpired:
        _fail('image_conversion_timeout', kind, '转换超过 15 秒，已停止本张图片。')
    if result.returncode:
        _fail('image_conversion_failed', kind, '转换失败：文件可能损坏，或包含转换器不支持的绘图指令。')


def _svg_png(data, output, kind):
    if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        _fail('image_external_resource_forbidden', kind, '包含不允许的 XML 实体或文档类型声明。')
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        _fail('invalid_image_data', kind, '的 SVG/XML 内容损坏，无法转换。')
    if root.tag.rsplit('}', 1)[-1] != 'svg':
        _fail('invalid_image_data', kind, '不是有效的 SVG 图形。')
    for element in root.iter():
        if element.tag.rsplit('}', 1)[-1] in ('script', 'foreignObject'):
            _fail('image_external_resource_forbidden', kind, '包含不允许的脚本或嵌入页面。')
        for name, value in element.attrib.items():
            if name.rsplit('}', 1)[-1] in ('href', 'base') and not (
                value.startswith('#')
                or re.fullmatch(r'data:image/(?:png|jpeg|gif|webp);base64,[A-Za-z0-9+/=\s]+', value)
            ):
                _fail('image_external_resource_forbidden', kind, '引用了外部资源；不读取外部地址。')
        # CSS can also refer to resources without href. Reject escaped CSS and
        # imports rather than attempting to implement a second CSS parser.
        css = ' '.join(element.attrib.values())
        if element.tag.rsplit('}', 1)[-1] == 'style':
            css += ' ' + (element.text or '')
        urls = re.findall(r'url\s*\(\s*([^)]*)\)', css, re.I)
        if '\\' in css or '@import' in css.lower() or any(not url.strip().strip('"\'').startswith('#') for url in urls):
            _fail('image_external_resource_forbidden', kind, '包含外部资源样式；不读取外部地址。')
    # stdin has no base URI: librsvg cannot resolve files relative to the
    # document, and its own loader forbids HTTP resources. Never use --unlimited.
    _run(
        [
            'rsvg-convert',
            '--format=png',
            f'--width={VECTOR_SIZE}',
            f'--height={VECTOR_SIZE}',
            '--keep-aspect-ratio',
            f'--output={output}',
        ],
        kind,
        data=data,
    )


def normalize_image(data, reference, *, max_bytes, max_pixels):
    """Return validated bytes, suffix/MIME/dimensions and original format."""
    kind = format_hint(reference)
    vector_kind = None
    if data.startswith(b'\x1f\x8b') and kind in ('WMZ', 'EMZ', 'SVGZ'):
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
                data = stream.read(max_bytes + 1)
        except (OSError, EOFError):
            _fail('invalid_image_data', kind, '的压缩数据损坏。')
        if len(data) > max_bytes:
            _fail('image_size_exceeded', kind, '解压后超过单张图片处理上限。')
    if data.startswith(b'\xd7\xcd\xc6\x9a') or data[:6] in (b'\x01\x00\x09\x00\x00\x03', b'\x02\x00\x09\x00\x00\x03'):
        vector_kind = 'WMF'
    elif data[:4] == b'\x01\x00\x00\x00' and data[40:44] == b' EMF':
        vector_kind = 'EMF'
    elif data.lstrip(b'\xef\xbb\xbf \t\r\n').startswith((b'<svg', b'<?xml', b'<!DOCTYPE')):
        vector_kind = 'SVG'
    elif data.startswith(b'%!PS'):
        vector_kind = 'EPS'
    elif data.startswith(b'%PDF-'):
        vector_kind = 'PDF'

    if vector_kind:
        kind = vector_kind
        with tempfile.TemporaryDirectory(prefix='image-convert-') as directory:
            source, output = Path(directory) / ('input.' + kind.lower()), Path(directory) / 'output.png'
            source.write_bytes(data)
            if kind == 'WMF':
                _run(
                    [
                        'wmf2gd',
                        '-t',
                        'png',
                        f'--maxwidth={VECTOR_SIZE}',
                        f'--maxheight={VECTOR_SIZE}',
                        '--maxpect',
                        '-o',
                        str(output),
                        str(source),
                    ],
                    kind,
                )
            elif kind == 'EMF':
                svg = Path(directory) / 'output.svg'
                _run(
                    [
                        'emf2svg-conv',
                        '-p',
                        '-i',
                        str(source),
                        '-o',
                        str(svg),
                        '-w',
                        str(VECTOR_SIZE),
                        '-h',
                        str(VECTOR_SIZE),
                    ],
                    kind,
                )
                if not svg.exists() or svg.stat().st_size > max_bytes:
                    _fail('image_conversion_failed', kind, '转换结果缺失或超过大小上限。')
                _svg_png(svg.read_bytes(), output, kind)
            elif kind == 'SVG':
                _svg_png(data, output, kind)
            else:
                _run(
                    [
                        'gs',
                        '-dSAFER',
                        '-dBATCH',
                        '-dNOPAUSE',
                        '-dFirstPage=1',
                        '-dLastPage=1',
                        '-sDEVICE=pngalpha',
                        '-dEPSCrop',
                        '-dFitPage',
                        '-dPDFFitPage',
                        f'-g{VECTOR_SIZE}x{VECTOR_SIZE}',
                        f'-sOutputFile={output}',
                        str(source),
                    ],
                    kind,
                )
            if not output.exists() or output.stat().st_size > max_bytes:
                _fail('image_conversion_failed', kind, '转换结果缺失或超过大小上限。')
            converted = output.read_bytes()
        normalized, suffix, mime, width, height, _ = normalize_image(
            converted, 'converted.png', max_bytes=max_bytes, max_pixels=max_pixels
        )
        return normalized, suffix, mime, width, height, kind

    if data[4:8] == b'ftyp' and data[8:12] in (b'heic', b'heix', b'hevc', b'hevx', b'mif1', b'msf1'):
        kind = 'HEIF'
        try:
            from pillow_heif import register_heif_opener

            register_heif_opener()
        except ImportError:
            _fail('image_decoder_unavailable', kind, '暂时不支持：缺少 HEIC/HEIF 解码器。')
    try:
        with Image.open(io.BytesIO(data)) as image:
            actual, (width, height) = image.format, image.size
            if width * height > max_pixels:
                _fail('image_pixel_limit_exceeded', actual or kind, f'分辨率超过 {max_pixels:,} 像素处理上限。')
            if actual in NATIVE_FORMATS:
                suffix, mime = NATIVE_FORMATS[actual]
                image.verify()
                return data, suffix, mime, width, height, actual
            if actual not in RASTER_FORMATS:
                _fail('unsupported_image_format', actual or kind, '格式暂不支持；原文件和可用正文仍保留。')
            image = ImageOps.exif_transpose(image).convert('RGBA')
            buffer = io.BytesIO()
            image.save(buffer, format='PNG')
            converted = buffer.getvalue()
            if len(converted) > max_bytes:
                _fail('image_size_exceeded', actual, '转换后的 PNG 超过单张图片处理上限。')
            return converted, 'png', 'image/png', *image.size, actual
    except UnidentifiedImageError:
        known = {
            *NATIVE_FORMATS,
            *RASTER_FORMATS,
            'JPG',
            'JPE',
            'TIF',
            'HEIC',
            'WMF',
            'EMF',
            'SVG',
            'EPS',
            'PDF',
            'WMZ',
            'EMZ',
            'SVGZ',
            'JP2',
            'J2K',
            'PBM',
            'PGM',
            'PNM',
        }
        if kind in known:
            _fail('invalid_image_data', kind, '无法解码：文件已损坏、不完整，或实际内容与格式不符。')
        _fail('unsupported_image_format', kind, '格式不受支持，无法生成图片检索内容；原文件和正文仍保留。')
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        _fail('image_pixel_limit_exceeded', kind, '分辨率超过安全处理上限。')
    except (OSError, SyntaxError):
        _fail('invalid_image_data', kind, '数据损坏或不完整，无法解码。')
