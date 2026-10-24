"""Safe user-facing image diagnostics, without paths, tokens or service bodies."""

import re
from pathlib import PurePosixPath


class ImageProcessingError(ValueError):
    def __init__(self, code, image_format, message, *, stage='conversion'):
        self.code, self.image_format, self.message, self.stage = code, image_format, message, stage
        super().__init__(message)


def format_hint(reference):
    suffix = PurePosixPath(str(reference).split('?', 1)[0]).suffix.lstrip('.').upper()
    return suffix if re.fullmatch(r'[A-Z0-9+-]{1,16}', suffix) else '未知'


def image_issue(exc, *, reference='', stage='extraction'):
    if isinstance(exc, ImageProcessingError):
        return {'reason': exc.code, 'format': exc.image_format, 'stage': exc.stage, 'message': exc.message}
    image_format = format_hint(reference)
    labels = {'extraction': '提取', 'conversion': '转换', 'encoding': '编码'}
    label = labels.get(stage, '处理')
    status = (
        getattr(exc, 'status', None)
        or getattr(exc, 'status_code', None)
        or getattr(getattr(exc, 'response', None), 'status_code', None)
    )
    if isinstance(exc, TimeoutError):
        code, message = 'image_processing_timeout', f'{image_format} 图片{label}超时。'
    elif isinstance(exc, PermissionError):
        code, message = 'image_access_denied', f'{image_format} 图片无法读取：文件访问权限不足。'
    elif isinstance(exc, FileNotFoundError):
        code, message = 'image_file_missing', f'{image_format} 图片资源缺失。'
    elif isinstance(status, int) and 400 <= status <= 599:
        code, message = 'image_service_error', f'{image_format} 图片{label}服务返回 HTTP {status}。'
    elif isinstance(exc, ValueError) and str(exc).isidentifier():
        code = str(exc)
        reasons = {
            'image_size_exceeded': '单张图片超过处理大小上限',
            'document_image_budget_exceeded': '图片数量或总大小超过处理上限',
            'unsupported_image_or_pixel_limit': '图片格式不受支持或分辨率超过处理上限',
            'resource_origin_not_allowed': '图片资源地址不在已授权服务范围内',
            'resource_redirect_not_allowed': '图片资源地址发生了不允许的跳转',
            'image_resource_missing': '解析服务没有返回图片数据',
            'invalid_image_data_uri': '图片内嵌数据格式无效',
            'unsafe_image_reference': '图片资源引用不安全',
            'office_xml_entities_forbidden': 'Office 嵌图所在 XML 含有不允许的实体声明',
            'office_archive_budget_exceeded': 'Office 压缩包解压大小或文件数量超过处理上限',
            'office_duplicate_archive_entries': 'Office 压缩包含有冲突的重复文件',
            'office_xml_too_large': 'Office 嵌图所在 XML 超过大小上限',
            'parser_image_resources_missing': '解析服务没有提供文中图片的数据',
            'invalid_parser_images': '解析服务返回的图片列表格式无效',
            'selected_parser_has_no_image_assets': '当前解析器不提供图片提取，正文仍可使用',
            'pdf_page_limit_exceeded': 'PDF 图片提取超过页数上限',
        }
        message = f'{image_format} 图片{label}失败：{reasons.get(code, "图片数据无效或当前编码空间不兼容")}。'
    else:
        code, message = (
            'image_' + stage + '_failed',
            f'{image_format} 图片{label}失败（{type(exc).__name__}），可能已损坏或缺少解码支持。',
        )
    return {'reason': code, 'format': image_format, 'stage': stage, 'message': message}
