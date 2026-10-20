"""Small, explicit deployment configuration. No secret defaults."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit
from cryptography.fernet import Fernet


@dataclass(frozen=True)
class Settings:
    public_base_url: str
    data_dir: Path
    encryption_key: str = field(repr=False)
    allowed_redirect_origins: tuple[str, ...] = ()
    access_token_seconds: int = 3600
    refresh_token_seconds: int = 2592000
    login_session_seconds: int = 28800
    allow_insecure_localhost: bool = False
    upstream_timeout_seconds: int = 30

    def __post_init__(self):
        parsed = urlsplit(self.public_base_url)
        local = self.allow_insecure_localhost and parsed.hostname in {'127.0.0.1', 'localhost', '::1'}
        if (parsed.scheme != 'https' and not (local and parsed.scheme == 'http')) or not parsed.hostname:
            raise ValueError('PUBLIC_BASE_URL必须为HTTPS；仅本地测试可显式允许HTTP')
        if parsed.username or parsed.password or parsed.path not in {'', '/'} or parsed.query or parsed.fragment:
            raise ValueError('PUBLIC_BASE_URL应为独立域名的根地址，不含路径、凭证或查询参数')
        object.__setattr__(self, 'public_base_url', self.public_base_url.rstrip('/'))
        Fernet(self.encryption_key.encode('ascii'))
        for origin in self.allowed_redirect_origins:
            value = urlsplit(origin)
            if value.scheme != 'https' or not value.hostname or value.path not in {'', '/'} or value.query or value.fragment or value.username:
                raise ValueError('允许的云端OAuth回调来源必须为HTTPS根地址')

    @property
    def cookie_secure(self):
        return self.public_base_url.startswith('https://')

    @classmethod
    def from_env(cls):
        key = os.environ.get('TRAVEL_ENCRYPTION_KEY', '')
        if not key:
            raise ValueError('缺少TRAVEL_ENCRYPTION_KEY，请先运行python -m travel_mcp generate-key')
        return cls(
            public_base_url=os.environ.get('PUBLIC_BASE_URL', 'https://travel-assiant-mcp.st-arn.com'),
            data_dir=Path(os.environ.get('TRAVEL_DATA_DIR', './data')),
            encryption_key=key,
            allowed_redirect_origins=tuple(x.strip().rstrip('/') for x in os.environ.get('OAUTH_REDIRECT_ORIGINS', '').split(',') if x.strip()),
            allow_insecure_localhost=os.environ.get('ALLOW_INSECURE_LOCALHOST') == '1',
        )
