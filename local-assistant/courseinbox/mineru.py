"""Hosted MinerU v4 adapter, adapted from Elias-2z/CampusInbox 1d02252.
Uses the existing stdlib runtime; credentials are restricted to the API host.
"""
import io
import json
import os
import re
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

MAX_RESULT_BYTES = 40 * 1024 * 1024
MAX_TEXT_BYTES = 1024 * 1024

class MinerUError(Exception):
    pass

class MinerUFailed(MinerUError):
    """The batch is terminal; a retry may submit a new batch."""

def api_key(root=None):
    value = os.environ.get('MINERU_API_KEY', '').strip()
    if value:
        return value
    try:
        for line in (Path(root or Path(__file__).resolve().parents[1]) / '.env').read_text(encoding='utf-8-sig').splitlines():
            match = re.fullmatch(r'\s*MINERU_API_KEY\s*=\s*(.*?)\s*', line)
            if match:
                return match[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ''

def safe_url(value: str) -> str:
    if not isinstance(value, str):
        raise MinerUError("MinerU 返回了无效的文件地址，已停止传输。")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise MinerUError("MinerU 返回了无效的文件地址，已停止传输。") from None
    host = parsed.hostname or ""
    trusted = (
        host == "cdn-mineru.openxlab.org.cn"
        or host == "mineru.net"
        or host.endswith((".mineru.net", ".aliyuncs.com"))
    )
    if (
        parsed.scheme != "https"
        or not trusted
        or parsed.username
        or parsed.password
        or port not in (None, 443)
    ):
        raise MinerUError("MinerU 返回了不受支持的文件地址，已停止传输。")
    return value


def markdown_from_zip(content: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            candidates = [i for i in archive.infolist() if Path(i.filename).name == "full.md"]
            if len(candidates) != 1 or candidates[0].file_size > MAX_TEXT_BYTES:
                raise MinerUError("解析结果缺少正文或正文过大，请拆分文件后重试。")
            # Read bounded text members in memory; never extract remote paths to disk.
            with archive.open(candidates[0]) as member:
                raw = member.read(MAX_TEXT_BYTES + 1)
            if len(raw) > MAX_TEXT_BYTES:
                raise MinerUError("解析正文过大，请拆分文件后重试。")
            text = raw.decode("utf-8-sig").strip()
            text = restore_margin_text(archive, text)
            if not text:
                raise MinerUError("没有识别到文字，请换一张清晰图片或手动填写。")
            return text
    except (zipfile.BadZipFile, UnicodeError, RuntimeError, OSError) as exc:
        raise MinerUError("MinerU 结果文件无法读取，请重试。") from exc


def restore_margin_text(archive: zipfile.ZipFile, body: str) -> str:
    """Markdown can omit course names classified as headers. Retain their source text."""
    candidates = [i for i in archive.infolist() if i.filename.endswith("_content_list.json")]
    if len(candidates) != 1 or candidates[0].file_size > MAX_TEXT_BYTES:
        return body
    try:
        with archive.open(candidates[0]) as member:
            raw = member.read(MAX_TEXT_BYTES + 1)
        if len(raw) > MAX_TEXT_BYTES:
            return body
        blocks = json.loads(raw.decode("utf-8-sig"))
    except (ValueError, RuntimeError, OSError, zipfile.BadZipFile):
        return body
    if not isinstance(blocks, list):
        return body
    headers, footers = [], []
    seen = re.sub(r"\s+", "", body)
    for block in blocks:
        if not isinstance(block, dict) or block.get("type") not in ("header", "footer"):
            continue
        value = block.get("text")
        if not isinstance(value, str) or not value.strip():
            continue
        value = value.strip()
        normalized = re.sub(r"\s+", "", value)
        if normalized in seen:
            continue
        (headers if block["type"] == "header" else footers).append(value)
        seen += normalized
    sections = []
    if headers:
        sections.append("【页眉文字】\n" + "\n".join(headers))
    if body:
        sections.append(body)
    if footers:
        sections.append("【页脚文字】\n" + "\n".join(footers))
    text = "\n\n".join(sections)
    if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
        raise MinerUError("解析正文过大，请拆分文件后重试。")
    return text



class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

class MinerUProvider:
    def __init__(self, root=None, opener=None):
        self.key = api_key(root)
        self.opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def request(self, method, url, data=None, headers=None, limit=MAX_RESULT_BYTES):
        request = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
        with self.opener.open(request, timeout=45) as response:
            content = response.read(limit + 1)
        if len(content) > limit:
            raise MinerUError('解析结果过大，请拆分文件后重试。')
        return content

    def api(self, method, path, payload=None):
        if not self.key:
            raise MinerUError('尚未配置 MinerU Token，请在项目根目录 .env 中设置 MINERU_API_KEY。')
        try:
            raw = self.request(method, 'https://mineru.net/api/v4/' + path,
                json.dumps(payload).encode() if payload is not None else None,
                {'Authorization': 'Bearer ' + self.key, 'Content-Type': 'application/json'}, limit=1024 * 1024)
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise MinerUError('MinerU Token 无效或已过期，请更新项目根目录 .env 中的配置。') from None
            raise
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise MinerUError('MinerU 返回的响应格式不正确，请稍后重试。')
        if result.get('code') != 0:
            if str(result.get('code')) in ('A0202', 'A0211'):
                raise MinerUError('MinerU Token 无效或已过期，请更新配置。')
            if str(result.get('code')) == '-60012':
                raise MinerUFailed('MinerU 解析任务已过期或不存在，请重新解析。')
            raise MinerUError('MinerU 未能处理请求，请检查额度、文件格式后重试。')
        if not isinstance(result.get('data'), dict):
            raise MinerUError('MinerU 返回的响应格式不正确，请稍后重试。')
        return result['data']

    def submit(self, path, filename, attachment_id):
        try:
            data = self.api('POST', 'file-urls/batch', {
                'files': [{'name': filename, 'data_id': attachment_id}],
                'model_version': 'vlm', 'language': 'ch',
            })
            batch_id = data['batch_id']
            if not isinstance(batch_id, str) or not re.fullmatch(r'[\w-]{1,128}', batch_id):
                raise MinerUError('MinerU 返回了无效的解析任务。')
            self.request('PUT', safe_url(data['file_urls'][0]), Path(path).read_bytes())
            return batch_id
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            raise MinerUError('连接 MinerU 或上传文件失败，请检查网络后重试。') from None

    def poll(self, batch_id):
        try:
            if not isinstance(batch_id, str) or not re.fullmatch(r'[\w-]{1,128}', batch_id):
                raise MinerUFailed('解析任务无效，请重新解析。')
            data = self.api('GET', 'extract-results/batch/' + batch_id)
            result = data['extract_result'][0]
            if result['state'] == 'failed':
                raise MinerUFailed('MinerU 解析失败，请检查文件是否清晰、完整，或转为 PDF 重试。')
            if result['state'] != 'done':
                return None
            return markdown_from_zip(self.request('GET', safe_url(result['full_zip_url'])))
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            raise MinerUError('暂时无法获取 MinerU 结果，可稍后继续查询。') from None
