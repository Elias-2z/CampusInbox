"""Resolve UI assets without exposing arbitrary project files."""
from pathlib import Path


def ui_asset(root, path):
    web = Path(root) / 'web'
    if path in ('/', '/index.html'):
        built = web / 'ui' / 'index.html'
        return built if built.is_file() else web / 'index.html'
    if path == '/legacy.html':
        return web / 'index.html'
    if path in ('/integration.css', '/integration-bridge.js'):
        return web / path[1:]
    if path.startswith('/assets/'):
        assets = (web / 'ui' / 'assets').resolve()
        candidate = (web / 'ui' / path[1:]).resolve()
        if candidate.is_relative_to(assets) and candidate.is_file():
            return candidate
    return None
