"""Build only reviewed MCP source/configuration; never traverse runtime data or secrets."""

import hashlib
import json
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
FILES = [
    'mcp-service/README.md', 'mcp-service/requirements.txt', 'mcp-service/requirements-test.txt',
    'mcp-service/pyproject.toml', 'mcp-service/Dockerfile', 'mcp-service/compose.yaml',
    'mcp-service/.env.example', 'mcp-service/.dockerignore',
    'mcp-service/deploy/1Panel部署.md', 'mcp-service/deploy/openresty-location.conf',
    'connectors/soco-travel-assistant/connector-meta.json',
    'connectors/soco-travel-assistant/mcp.json', 'connectors/soco-travel-assistant/icon.svg',
] + [f'mcp-service/travel_mcp/{name}.py' for name in (
    '__init__', '__main__', 'config', 'store', 'workflow', 'auth', 'upstream', 'actions', 'server')]
FILES += [f'mcp-service/tests/test_{name}.py' for name in ('store_workflow', 'auth', 'gateway', 'server')]


def build():
    for relative in FILES:
        path = ROOT / relative
        if not path.is_file() or '.venv' in path.parts or path.name == '.env':
            raise ValueError('Unexpected package path: ' + relative)
    meta = json.loads((ROOT / 'connectors/soco-travel-assistant/connector-meta.json').read_text('utf-8'))
    assert meta['version'] == '2.0.0' and 'auth_mode' not in meta
    config = json.loads((ROOT / 'connectors/soco-travel-assistant/mcp.json').read_text('utf-8'))
    assert config['mcpServers']['soco-travel-assistant']['url'] == 'https://travel-assiant-mcp.st-arn.com/mcp'
    output = ROOT / 'travel-assistant-mcp-v2.0.0.zip'
    with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        for relative in FILES:
            archive.write(ROOT / relative, relative)
    with ZipFile(output) as archive:
        assert archive.testzip() is None
        assert set(archive.namelist()) == set(FILES)
        assert not any(x.endswith(('.sqlite3', '.db', '/.env')) or '/.venv/' in x for x in archive.namelist())
    print(json.dumps({'package': output.name, 'files': len(FILES), 'bytes': output.stat().st_size,
        'sha256': hashlib.sha256(output.read_bytes()).hexdigest(), 'runtime_data_excluded': True}, ensure_ascii=False))


if __name__ == '__main__':
    build()
