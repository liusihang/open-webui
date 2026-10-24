"""Real decoder/converter acceptance, plus optional-image diagnostic contracts."""

import gzip
import io
import os
from pathlib import Path
import shutil
import struct
import subprocess
from unittest.mock import patch

import pytest
from PIL import Image

from open_webui.retrieval.document_image_assets import ImageAssetStore
from open_webui.retrieval.image_errors import ImageProcessingError, image_issue
from open_webui.retrieval.image_normalization import normalize_image


def normalized(data, name):
    return normalize_image(data, name, max_bytes=20 * 1024**2, max_pixels=40_000_000)


@pytest.mark.parametrize('kind', ['PNG', 'JPEG', 'WEBP', 'GIF', 'TIFF', 'BMP'])
def test_native_formats_preserve_bytes(kind):
    source = io.BytesIO()
    Image.new('RGB', (32, 24), 'red').save(source, format=kind)
    result = normalized(source.getvalue(), 'misleading.svg')
    assert result[0] == source.getvalue()
    assert result[3:5] == (32, 24)


@pytest.mark.parametrize('kind', ['AVIF', 'ICO', 'ICNS', 'JPEG2000', 'PCX', 'PPM', 'TGA', 'DIB', 'DDS'])
def test_additional_raster_formats_become_png(kind):
    source = io.BytesIO()
    Image.new('RGB', (64, 64), 'red').save(source, format=kind)
    result = normalized(source.getvalue(), 'image.' + kind.lower())
    assert result[1:3] == ('png', 'image/png')
    assert result[5] == kind
    with Image.open(io.BytesIO(result[0])) as image:
        assert image.format == 'PNG'


def test_psd_composite():
    data = struct.pack('>4sH6sHIIHH', b'8BPS', 1, b'\x00' * 6, 3, 2, 2, 8, 3)
    data += b'\x00' * 14 + b'\xff' * 4 + b'\x00' * 8
    assert normalized(data, 'image.psd')[1:3] == ('png', 'image/png')


def test_mpo_first_frame():
    source = io.BytesIO()
    Image.new('RGB', (32, 24), 'red').save(
        source, format='MPO', save_all=True, append_images=[Image.new('RGB', (32, 24), 'blue')]
    )
    assert normalized(source.getvalue(), 'image.mpo')[1:3] == ('png', 'image/png')


def wmf():
    records = b''.join(
        [
            struct.pack('<IHhh', 5, 0x020B, 0, 0),
            struct.pack('<IHhh', 5, 0x020C, 100, 100),
            struct.pack('<IH4h', 7, 0x041B, 90, 90, 10, 10),
            struct.pack('<IH', 3, 0),
        ]
    )
    header = struct.pack('<HHHIHIH', 1, 9, 0x300, (18 + len(records)) // 2, 0, 7, 0)
    placeable = struct.pack('<IHhhhhHI', 0x9AC6CDD7, 0, 0, 0, 100, 100, 1440, 0)
    checksum = 0
    for word in struct.unpack('<10H', placeable):
        checksum ^= word
    return placeable + struct.pack('<H', checksum) + header + records


def emf():
    rectangle = struct.pack('<II4i', 43, 24, 10, 10, 90, 90)
    end = struct.pack('<IIIII', 14, 20, 0, 0, 20)
    header = struct.pack(
        '<II4i4iIIIIHHIII2i2iIII2i',
        1,
        108,
        0,
        0,
        100,
        100,
        0,
        0,
        2646,
        2646,
        0x464D4520,
        0x10000,
        108 + len(rectangle) + len(end),
        3,
        1,
        0,
        0,
        0,
        0,
        100,
        100,
        26,
        26,
        0,
        0,
        0,
        26000,
        26000,
    )
    return header + rectangle + end


SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="60"><rect width="100" height="60" fill="red"/></svg>'
)
EPS = b'%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 100 60\n1 0 0 setrgbcolor 0 0 100 60 rectfill\nshowpage\n'


@pytest.mark.parametrize(
    'kind,executable',
    [
        ('WMF', 'wmf2gd'),
        ('WMZ', 'wmf2gd'),
        ('EMF', 'emf2svg-conv'),
        ('EMZ', 'emf2svg-conv'),
        ('SVG', 'rsvg-convert'),
        ('SVGZ', 'rsvg-convert'),
        ('EPS', 'gs'),
    ],
)
def test_real_vector_conversion(kind, executable):
    if not shutil.which(executable):
        if os.getenv('REQUIRE_IMAGE_CONVERTERS'):
            pytest.fail('required converter missing: ' + executable)
        pytest.skip('converter acceptance runs in isolated Slim image')
    data = {
        'WMF': wmf,
        'WMZ': lambda: gzip.compress(wmf()),
        'EMF': emf,
        'EMZ': lambda: gzip.compress(emf()),
        'SVG': lambda: SVG,
        'SVGZ': lambda: gzip.compress(SVG),
        'EPS': lambda: EPS,
    }[kind]()
    result = normalized(data, 'image.' + kind.lower())
    assert result[1:3] == ('png', 'image/png')
    assert 0 < result[3] <= 2048 and 0 < result[4] <= 2048


@pytest.mark.parametrize(
    'content',
    [
        b'<!DOCTYPE svg><svg/>',
        b'<svg><image href="file:///etc/passwd"/></svg>',
        b'<svg><image href="https://example.org/image.png"/></svg>',
        b'<svg><style>@import "https://example.org/style";</style></svg>',
        b'<svg><rect fill="url(file:///etc/passwd)"/></svg>',
        b'<svg><foreignObject/></svg>',
        b'<svg><image href="data:image/svg+xml;base64,PHN2Zy8+"/></svg>',
    ],
)
def test_svg_external_resources_are_rejected_without_running_renderer(content):
    with patch('subprocess.run', side_effect=AssertionError('renderer must not run')):
        with pytest.raises(ImageProcessingError) as error:
            normalized(content, 'image.svg')
    assert error.value.code == 'image_external_resource_forbidden'


def test_converter_missing_is_specific():
    with patch('shutil.which', return_value=None):
        with pytest.raises(ImageProcessingError) as error:
            normalized(wmf(), 'image.wmf')
    issue = image_issue(error.value)
    assert issue['format'] == 'WMF'
    assert issue['reason'] == 'image_converter_unavailable'
    assert '转换器' in issue['message']


def test_converter_timeout_is_specific():
    with (
        patch('shutil.which', return_value='/usr/bin/wmf2gd'),
        patch('subprocess.run', side_effect=subprocess.TimeoutExpired('wmf2gd', 15)),
    ):
        with pytest.raises(ImageProcessingError) as error:
            normalized(wmf(), 'image.wmf')
    assert error.value.code == 'image_conversion_timeout'
    assert '15 秒' in error.value.message


def test_heic_real_decoder():
    if os.getenv('REQUIRE_IMAGE_CONVERTERS'):
        from pillow_heif import register_heif_opener
    else:
        register_heif_opener = pytest.importorskip('pillow_heif').register_heif_opener
    register_heif_opener()
    source = io.BytesIO()
    Image.new('RGB', (32, 24), 'red').save(source, format='HEIF')
    assert normalized(source.getvalue(), 'image.heic')[1:3] == ('png', 'image/png')


def test_embedded_pdf_first_page_conversion():
    if not shutil.which('gs'):
        if os.getenv('REQUIRE_IMAGE_CONVERTERS'):
            pytest.fail('required converter missing: gs')
        pytest.skip('converter acceptance runs in isolated Slim image')
    source = io.BytesIO()
    Image.new('RGB', (32, 24), 'red').save(source, format='PDF')
    assert normalized(source.getvalue(), 'graphic.pdf')[1:3] == ('png', 'image/png')


def test_corrupt_and_unknown_are_not_false_success():
    for name in ('broken.png', 'unsupported.xyz'):
        with pytest.raises(ImageProcessingError) as error:
            normalized(b'not an image', name)
        assert name.split('.')[-1].upper() in error.value.message


def test_deferred_store_does_not_decode_or_render(tmp_path):
    store = ImageAssetStore(tmp_path, 'file', defer_conversion=True)
    with patch(
        'open_webui.retrieval.document_image_assets.normalize_image',
        side_effect=AssertionError('decode before publication'),
    ):
        result = store.put(wmf(), backend='office', origin_reference='word/media/image.wmf')
    assert result['needs_conversion'] is True
    assert Path(result['storage_uri']).read_bytes() == wmf()


def test_diagnostics_do_not_leak_remote_secrets():
    issue = image_issue(RuntimeError('https://secret.example/?token=private'), reference='image.heic')
    assert issue['format'] == 'HEIC'
    assert 'secret' not in str(issue) and 'private' not in str(issue)
