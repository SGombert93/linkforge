from dataclasses import dataclass, field
from pathlib import Path
import os
from urllib.parse import urlsplit

@dataclass
class Settings:
    secret: str = field(default_factory=lambda: os.getenv('SESSION_SECRET', ''))
    base_url: str = field(default_factory=lambda: os.getenv('BASE_URL', 'http://localhost:8000').rstrip('/'))
    data_dir: Path = field(default_factory=lambda: Path(os.getenv('DATA_DIR', './data')))
    discovery_url: str = field(default_factory=lambda: os.getenv('OIDC_DISCOVERY_URL', ''))
    client_id: str = field(default_factory=lambda: os.getenv('OIDC_CLIENT_ID', ''))
    client_secret: str = field(default_factory=lambda: os.getenv('OIDC_CLIENT_SECRET', ''))
    role_claim: str = field(default_factory=lambda: os.getenv('OIDC_ROLE_CLAIM', 'roles'))
    role_source: str = field(default_factory=lambda: os.getenv('OIDC_ROLE_SOURCE', 'id_token'))
    scope: str = field(default_factory=lambda: os.getenv('OIDC_SCOPES', 'openid profile email'))
    session_seconds: int = field(default_factory=lambda: int(os.getenv('SESSION_SECONDS', '3600')))

    def validate(self):
        if len(self.secret) < 32 or self.secret.startswith('replace-with-'):
            raise RuntimeError('Set SESSION_SECRET to at least 32 random characters.')
        p = urlsplit(self.base_url)
        if p.scheme not in ('https', 'http') or not p.hostname or p.path or p.query or p.fragment or p.username:
            raise RuntimeError('BASE_URL must be an origin, e.g. https://music.example.com')
        if p.scheme != 'https' and p.hostname not in ('localhost', '127.0.0.1', 'testserver'):
            raise RuntimeError('Use HTTPS except for local development.')
        if self.role_source not in ('id_token', 'userinfo'):
            raise RuntimeError('OIDC_ROLE_SOURCE must be id_token or userinfo.')
        if self.discovery_url and not self.discovery_url.startswith('https://'):
            raise RuntimeError('OIDC discovery requires HTTPS.')
        if not 60 <= self.session_seconds <= 86400:
            raise RuntimeError('SESSION_SECONDS must be between 60 and 86400.')
