"""OAuth transport identity and a browser-only private credential portal.

The MCP tools obtain identity only from the SDK's verified access-token context.
The portal has no registration route: operators create accounts through Store.
"""
from __future__ import annotations

import hashlib
import hmac
import html
import json
import re
import secrets
import time
from urllib.parse import unquote, urlencode, urlsplit

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.middleware.client_auth import ClientAuthenticator
from mcp.server.auth.handlers.token import TokenHandler
from mcp.server.auth.provider import (
    AccessToken, AuthorizationCode, AuthorizationParams, AuthorizeError,
    OAuthAuthorizationServerProvider, RefreshToken, RegistrationError, TokenError,
    construct_redirect_uri,
)
from mcp.server.auth.routes import build_metadata, cors_middleware
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.server.transport_security import DEFAULT_MAX_REQUEST_BODY_SIZE, RequestBodyLimitMiddleware
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyHttpUrl
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.routing import Route


_PROVIDERS = {"tencentmap": "key", "didi": "key", "tongcheng": "key", "dingtalk": "token"}
_WORKBUDDY_PATH = "/mcp/connector:soco-travel-assistant/oauth/callback"
_COOKIE = "travel_session"
_CODE_LIFETIME = 300
_PORTAL_LIMIT = 16384
_HEADERS = {"Cache-Control": "no-store", "Pragma": "no-cache", "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": "default-src 'none'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
            "X-Content-Type-Options": "nosniff"}


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _opaque() -> str:
    return secrets.token_urlsafe(32)


def current_user_id() -> str:
    """Return the resource owner from the verified SDK request context only."""
    token = get_access_token()
    if token is None or not token.subject:
        raise PermissionError("Authentication required")
    return token.subject


class TravelOAuthProvider(OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]):
    def __init__(self, store, settings):
        self.store = store
        self.settings = settings
        self.base = settings.public_base_url.rstrip("/")
        self.resource = self.base + "/mcp"
        with store.connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS oauth_clients (
                    client_id TEXT PRIMARY KEY, metadata TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS oauth_flows (
                    hash TEXT PRIMARY KEY, client_id TEXT NOT NULL, params TEXT NOT NULL,
                    expires_at INTEGER NOT NULL, session_hash TEXT
                );
                CREATE TABLE IF NOT EXISTS oauth_codes (
                    hash TEXT PRIMARY KEY, data TEXT NOT NULL, client_id TEXT NOT NULL,
                    subject TEXT NOT NULL, expires_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS oauth_tokens (
                    hash TEXT PRIMARY KEY, kind TEXT NOT NULL, client_id TEXT NOT NULL,
                    subject TEXT NOT NULL, scopes TEXT NOT NULL, resource TEXT NOT NULL,
                    expires_at INTEGER NOT NULL, family TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS oauth_tokens_family ON oauth_tokens(family);
                CREATE TABLE IF NOT EXISTS oauth_sessions (
                    hash TEXT PRIMARY KEY, user_id TEXT, csrf TEXT NOT NULL, expires_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS oauth_login_attempts (
                    key TEXT PRIMARY KEY, started_at INTEGER NOT NULL, failures INTEGER NOT NULL
                );
            """)

    def _allowed_redirect(self, raw: str) -> bool:
        try:
            uri = urlsplit(raw)
            if uri.username is not None or uri.password is not None or uri.fragment or uri.query:
                return False
            # Accessing port validates its spelling/range, including IPv6 brackets.
            _ = uri.port
            if uri.scheme == "workbuddy":
                return uri.netloc == "workbuddy" and unquote(uri.path) == _WORKBUDDY_PATH
            if uri.scheme in ("http", "https") and uri.hostname in ("localhost", "127.0.0.1", "::1"):
                return uri.path == "/oauth/callback"
            origin = f"{uri.scheme}://{uri.netloc}"
            return uri.scheme == "https" and origin in self.settings.allowed_redirect_origins
        except (ValueError, TypeError):
            return False

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if not client_info.client_id or not client_info.redirect_uris or any(
            not self._allowed_redirect(str(uri)) for uri in client_info.redirect_uris
        ):
            raise RegistrationError("invalid_redirect_uri", "Redirect URI is not approved")
        if (client_info.token_endpoint_auth_method != "none" or client_info.client_secret
                or set(client_info.grant_types) - {"authorization_code", "refresh_token"}
                or "authorization_code" not in client_info.grant_types
                or client_info.response_types != ["code"]
                or set((client_info.scope or "travel").split()) != {"travel"}):
            raise RegistrationError("invalid_client_metadata", "A public PKCE client with travel scope is required")
        with self.store.connection() as conn:
            conn.execute("INSERT INTO oauth_clients(client_id, metadata) VALUES (?, ?)",
                         (client_info.client_id, client_info.model_dump_json()))

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        with self.store.connection() as conn:
            row = conn.execute("SELECT metadata FROM oauth_clients WHERE client_id=?", (client_id,)).fetchone()
        return OAuthClientInformationFull.model_validate_json(row[0]) if row else None

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        if (not self._allowed_redirect(str(params.redirect_uri))
                or params.redirect_uri not in (client.redirect_uris or [])):
            raise AuthorizeError("invalid_request", "Redirect URI is not approved")
        if params.resource is not None and params.resource != self.resource:
            raise AuthorizeError("invalid_target", "Unsupported resource")
        if set(params.scopes or ["travel"]) != {"travel"}:
            raise AuthorizeError("invalid_scope", "Unsupported scope")
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", params.code_challenge):
            raise AuthorizeError("invalid_request", "S256 PKCE is required")
        flow = _opaque()
        with self.store.connection() as conn:
            conn.execute("DELETE FROM oauth_flows WHERE expires_at<=?", (int(time.time()),))
            conn.execute("INSERT INTO oauth_flows(hash,client_id,params,expires_at) VALUES (?,?,?,?)",
                         (_hash(flow), client.client_id, params.model_dump_json(), int(time.time()) + _CODE_LIFETIME))
        return self.base + "/account/authorize?" + urlencode({"flow": flow})

    async def load_authorization_code(self, client, authorization_code):
        with self.store.connection() as conn:
            row = conn.execute("SELECT data,subject FROM oauth_codes WHERE hash=? AND client_id=? AND expires_at>?",
                               (_hash(authorization_code), client.client_id, time.time())).fetchone()
        if not row or not self.store.get_user(row[1]):
            return None
        return AuthorizationCode(code=authorization_code, **json.loads(row[0]))

    def _issue(self, conn, client_id, subject, scopes, resource):
        now = int(time.time())
        access, refresh, family = _opaque(), _opaque(), _opaque()
        for raw, kind, duration in ((access, "access", self.settings.access_token_seconds),
                                    (refresh, "refresh", self.settings.refresh_token_seconds)):
            conn.execute("INSERT INTO oauth_tokens VALUES (?,?,?,?,?,?,?,?)",
                         (_hash(raw), kind, client_id, subject, json.dumps(scopes), resource, now + duration, family))
        return OAuthToken(access_token=access, refresh_token=refresh, token_type="Bearer",
                          expires_in=self.settings.access_token_seconds, scope=" ".join(scopes))

    async def exchange_authorization_code(self, client, authorization_code):
        with self.store.connection() as conn:
            # Atomic consume: concurrent requests cannot exchange the same code twice.
            row = conn.execute("DELETE FROM oauth_codes WHERE hash=? AND client_id=? AND expires_at>? RETURNING data,subject",
                               (_hash(authorization_code.code), client.client_id, time.time())).fetchone()
            if not row or not self.store.get_user(row[1]):
                raise TokenError("invalid_grant", "Authorization code is invalid")
            code = json.loads(row[0])
            return self._issue(conn, client.client_id, row[1], code["scopes"], code["resource"])

    async def _load_token(self, raw, kind, client_id=None):
        with self.store.connection() as conn:
            row = conn.execute("SELECT * FROM oauth_tokens WHERE hash=? AND kind=? AND expires_at>?",
                               (_hash(raw), kind, time.time())).fetchone()
        if not row or (client_id is not None and row["client_id"] != client_id) or not self.store.get_user(row["subject"]):
            return None
        model = AccessToken if kind == "access" else RefreshToken
        fields = dict(token=raw, client_id=row["client_id"], subject=row["subject"],
                      scopes=json.loads(row["scopes"]), resource=row["resource"], expires_at=row["expires_at"])
        if kind == "access":
            fields["claims"] = {"iss": self.base}
        return model(**fields)

    async def load_access_token(self, token):
        return await self._load_token(token, "access")

    async def load_refresh_token(self, client, refresh_token):
        return await self._load_token(refresh_token, "refresh", client.client_id)

    async def exchange_refresh_token(self, client, refresh_token, scopes):
        with self.store.connection() as conn:
            row = conn.execute("SELECT * FROM oauth_tokens WHERE hash=? AND kind='refresh' AND client_id=? AND expires_at>?",
                               (_hash(refresh_token.token), client.client_id, time.time())).fetchone()
            if not row or not self.store.get_user(row["subject"]):
                raise TokenError("invalid_grant", "Refresh token is invalid")
            if not scopes or not set(scopes).issubset(json.loads(row["scopes"])):
                raise TokenError("invalid_scope", "Unsupported scope")
            # A compare-and-delete makes rotation safe even when requests race.
            changed = conn.execute("DELETE FROM oauth_tokens WHERE hash=? AND kind='refresh'", (_hash(refresh_token.token),)).rowcount
            if changed != 1:
                raise TokenError("invalid_grant", "Refresh token is invalid")
            conn.execute("DELETE FROM oauth_tokens WHERE family=?", (row["family"],))
            return self._issue(conn, client.client_id, row["subject"], scopes, row["resource"])

    async def revoke_token(self, token):
        with self.store.connection() as conn:
            row = conn.execute("SELECT family FROM oauth_tokens WHERE hash=?", (_hash(token.token),)).fetchone()
            if row:
                conn.execute("DELETE FROM oauth_tokens WHERE family=?", (row[0],))

    def _session(self, request: Request, create=False):
        raw = request.cookies.get(_COOKIE, "")
        row = None
        now = int(time.time())
        with self.store.connection() as conn:
            if raw and len(raw) <= 128:
                row = conn.execute("SELECT * FROM oauth_sessions WHERE hash=? AND expires_at>?", (_hash(raw), now)).fetchone()
            if row and row["user_id"] and not self.store.get_user(row["user_id"]):
                row = None
            if row:
                return dict(row), None
            if not create:
                return None, None
            raw = _opaque()
            value = {"hash": _hash(raw), "user_id": None, "csrf": _opaque(), "expires_at": now + 900}
            conn.execute("DELETE FROM oauth_sessions WHERE expires_at<=?", (now,))
            conn.execute("INSERT INTO oauth_sessions VALUES (?,?,?,?)", tuple(value.values()))
            return value, raw

    def _flow(self, raw, session_hash, bind=False):
        if not isinstance(raw, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", raw):
            return None
        with self.store.connection() as conn:
            if bind:
                conn.execute("UPDATE oauth_flows SET session_hash=? WHERE hash=? AND session_hash IS NULL AND expires_at>?",
                             (session_hash, _hash(raw), time.time()))
            row = conn.execute("SELECT * FROM oauth_flows WHERE hash=? AND session_hash=? AND expires_at>?",
                               (_hash(raw), session_hash, time.time())).fetchone()
        return dict(row) if row else None

    def _cookie(self, response, raw):
        if raw:
            response.set_cookie(_COOKIE, raw, max_age=self.settings.login_session_seconds,
                                httponly=True, secure=self.settings.cookie_secure, samesite="lax", path="/")
        return response

    def _limited(self, request, username, failed=False):
        address = request.client.host if request.client else "unknown"
        key = _hash(address + "\0" + username.casefold())
        now = int(time.time())
        with self.store.connection() as conn:
            conn.execute("DELETE FROM oauth_login_attempts WHERE started_at<=?", (now - 300,))
            row = conn.execute("SELECT failures FROM oauth_login_attempts WHERE key=?", (key,)).fetchone()
            if row and row[0] >= 5:
                return True
            if failed:
                conn.execute("INSERT INTO oauth_login_attempts VALUES (?,?,1) ON CONFLICT(key) DO UPDATE SET failures=failures+1", (key, now))
        return False


def _field(name, value):
    return f'<input type="hidden" name="{name}" value="{html.escape(str(value), quote=True)}">'


def _page(body, status=200):
    return HTMLResponse('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>行程助手个人配置</title><body>'
                        + body + '</body></html>', status_code=status, headers=_HEADERS)


def _login(session, flow="", message=""):
    return '<h1>登录行程助手</h1><p>' + html.escape(message) + '</p><form action="/account/login" method="post">' + _field("csrf", session["csrf"]) + _field("flow", flow) + (
        '<label>用户名 <input name="username" autocomplete="username" required></label>'
        '<label>密码 <input type="password" name="password" value="" autocomplete="current-password" required></label>'
        '<button type="submit">登录</button></form><p>账号由管理员创建。</p>')


async def _form(request):
    # Credentials never enter query strings, logs, or exception messages.
    if request.headers.get("content-type", "").split(";", 1)[0] != "application/x-www-form-urlencoded":
        return None
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > _PORTAL_LIMIT:
            return None
        body.extend(chunk)
    from urllib.parse import parse_qs
    try:
        values = parse_qs(bytes(body).decode("utf-8"), keep_blank_values=True, max_num_fields=12, errors="strict")
        if any(len(items) != 1 for items in values.values()):
            return None
        return {key: items[0] for key, items in values.items()}
    except (ValueError, UnicodeError):
        return None


def _csrf_ok(request, form, session, base):
    if not session or not form or not hmac.compare_digest(form.get("csrf", "").encode("utf-8"), session["csrf"].encode("utf-8")):
        return False
    origin = request.headers.get("origin")
    if origin is not None and origin != base:
        return False
    referer = request.headers.get("referer")
    if referer:
        parsed = urlsplit(referer)
        if f"{parsed.scheme}://{parsed.netloc}" != base:
            return False
    return True


def add_portal_routes(mcp, provider: TravelOAuthProvider, store, settings):
    """Register public browser endpoints, each enforcing its own session and CSRF."""
    @mcp.custom_route("/account", methods=["GET"])
    async def account(request):
        session, raw = provider._session(request, create=True)
        if not session["user_id"]:
            return provider._cookie(_page(_login(session)), raw)
        user = store.get_user(session["user_id"])
        statuses = store.credential_status(session["user_id"])
        body = '<h1>行程助手个人配置</h1><p>' + html.escape(user["username"]) + '</p>'
        for name, field in _PROVIDERS.items():
            status = statuses.get(name, False)
            if isinstance(status, dict):
                status = status.get("configured", False)
            body += '<section><h2>' + name + '</h2><p>' + ('已配置' if status else '未配置') + '</p>'
            body += '<form action="/account/credentials" method="post">' + _field("csrf", session["csrf"]) + _field("provider", name)
            body += f'<label>{field} <input type="password" name="{field}" value="" autocomplete="new-password"></label>'
            body += '<button name="action" value="save">保存</button><button name="action" value="delete">删除</button></form></section>'
        body += '<form action="/account/logout" method="post">' + _field("csrf", session["csrf"]) + '<button>退出登录</button></form>'
        return provider._cookie(_page(body), raw)

    @mcp.custom_route("/account/login", methods=["POST"])
    async def login(request):
        form = await _form(request)
        session, _ = provider._session(request)
        if not _csrf_ok(request, form, session, provider.base):
            return _page("请求无效，请刷新页面后重试。", 403)
        username = form.get("username", "")[:128]
        password = form.get("password", "")
        flow = form.get("flow", "")
        if flow and not provider._flow(flow, session["hash"]):
            return _page("授权流程已失效。", 400)
        if provider._limited(request, username):
            return _page("登录尝试过多，请稍后重试。", 429)
        uid = store.authenticate(username, password) if 0 < len(password) <= 1024 else None
        if not uid:
            provider._limited(request, username, failed=True)
            return _page(_login(session, flow, "用户名或密码错误。"), 401)
        raw = _opaque()
        new_hash = _hash(raw)
        with store.connection() as conn:
            conn.execute("DELETE FROM oauth_sessions WHERE hash=?", (session["hash"],))
            conn.execute("INSERT INTO oauth_sessions VALUES (?,?,?,?)",
                         (new_hash, uid, _opaque(), int(time.time()) + settings.login_session_seconds))
            conn.execute("UPDATE oauth_flows SET session_hash=? WHERE session_hash=?", (new_hash, session["hash"]))
        location = "/account/authorize?" + urlencode({"flow": flow}) if flow else "/account"
        return provider._cookie(RedirectResponse(location, status_code=303, headers=_HEADERS), raw)

    @mcp.custom_route("/account/credentials", methods=["POST"])
    async def credentials(request):
        form = await _form(request)
        session, _ = provider._session(request)
        if not _csrf_ok(request, form, session, provider.base):
            return _page("请求无效，请刷新页面后重试。", 403)
        if not session["user_id"]:
            return _page("请先登录。", 401)
        name = form.get("provider", "")
        action = form.get("action", "save")
        if name not in _PROVIDERS or action not in ("save", "delete"):
            return _page("配置无效。", 400)
        try:
            if action == "delete":
                store.delete_credentials(session["user_id"], name)
            else:
                field = _PROVIDERS[name]
                value = form.get(field, "").strip()
                if not value or len(value) > 8192:
                    return _page("配置无效。", 400)
                store.set_credentials(session["user_id"], name, {field: value})
        except Exception:
            # Store errors are deliberately not interpolated: upstream errors may contain keys.
            return _page("无法保存配置，请检查输入后重试。", 400)
        return RedirectResponse("/account", status_code=303, headers=_HEADERS)

    @mcp.custom_route("/account/logout", methods=["POST"])
    async def logout(request):
        form = await _form(request)
        session, _ = provider._session(request)
        if not _csrf_ok(request, form, session, provider.base):
            return _page("请求无效，请刷新页面后重试。", 403)
        with store.connection() as conn:
            conn.execute("DELETE FROM oauth_sessions WHERE hash=?", (session["hash"],))
        result = RedirectResponse("/account", status_code=303, headers=_HEADERS)
        result.delete_cookie(_COOKIE, path="/", secure=settings.cookie_secure, httponly=True, samesite="lax")
        return result

    @mcp.custom_route("/account/authorize", methods=["GET", "POST"])
    async def consent(request):
        session, raw = provider._session(request, create=request.method == "GET")
        if request.method == "POST":
            form = await _form(request)
            if not _csrf_ok(request, form, session, provider.base):
                return _page("请求无效，请刷新页面后重试。", 403)
            flow_id = form.get("flow", "")
        else:
            form = None
            flow_id = request.query_params.get("flow", "")
        flow = provider._flow(flow_id, session["hash"], bind=request.method == "GET") if session else None
        if not flow:
            return _page("授权流程已失效。", 400)
        if not session["user_id"]:
            if request.method == "POST":
                return _page("请先登录。", 401)
            return provider._cookie(_page(_login(session, flow_id)), raw)
        client = await provider.get_client(flow["client_id"])
        if not client:
            return _page("授权流程已失效。", 400)
        params = AuthorizationParams.model_validate_json(flow["params"])
        if request.method == "GET":
            redirect = urlsplit(str(params.redirect_uri))
            target = f"{redirect.scheme}://{redirect.netloc}"
            body = '<h1>授权行程助手</h1><p>客户端：' + html.escape(client.client_name or client.client_id) + '</p>'
            body += '<p>权限：travel（访问本人的行程与配置状态）</p><p>回调来源：' + html.escape(target) + '</p>'
            body += '<form action="/account/authorize" method="post">' + _field("csrf", session["csrf"]) + _field("flow", flow_id)
            body += '<button name="decision" value="approve">同意授权</button><button name="decision" value="deny">拒绝</button></form>'
            return provider._cookie(_page(body), raw)
        decision = form.get("decision", "")
        if decision not in ("approve", "deny"):
            return _page("请选择是否授权。", 400)
        code = _opaque()
        with store.connection() as conn:
            consumed = conn.execute("DELETE FROM oauth_flows WHERE hash=? AND session_hash=? AND expires_at>?",
                                    (_hash(flow_id), session["hash"], time.time())).rowcount
            if consumed != 1:
                return _page("授权流程已失效。", 400)
            if decision == "approve":
                value = AuthorizationCode(code=code, scopes=params.scopes or ["travel"],
                    expires_at=int(time.time()) + _CODE_LIFETIME, client_id=client.client_id,
                    code_challenge=params.code_challenge, redirect_uri=params.redirect_uri,
                    redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
                    resource=provider.resource, subject=session["user_id"])
                conn.execute("INSERT INTO oauth_codes VALUES (?,?,?,?,?)", (_hash(code),
                    value.model_dump_json(exclude={"code"}), client.client_id, session["user_id"], value.expires_at))
        location = construct_redirect_uri(str(params.redirect_uri), state=params.state, iss=provider.base,
                                         **({"code": code} if decision == "approve" else {"error": "access_denied"}))
        return RedirectResponse(location, status_code=303, headers=_HEADERS)


def finalize_oauth_routes(app, provider: TravelOAuthProvider):
    """Call after MCPServer.streamable_http_app(); preserve SDK PKCE and correct two SDK2 gaps.

    SDK2 metadata omits public authentication, and its token handler parses but does
    not enforce the resource parameter. Issued tokens here always target /mcp.
    """
    metadata = build_metadata(AnyHttpUrl(provider.base), None,
        ClientRegistrationOptions(enabled=True, valid_scopes=["travel"], default_scopes=["travel"]),
        RevocationOptions(enabled=False))
    document = metadata.model_dump(mode="json", exclude_none=True)
    document["issuer"] = provider.base
    document["token_endpoint_auth_methods_supported"] = ["none"]

    async def metadata_handler(request):
        return JSONResponse(document, headers={"Cache-Control": "no-store"})

    handler = TokenHandler(provider, ClientAuthenticator(provider))

    async def token_handler(request):
        form = await request.form()
        resources = form.getlist("resource")
        if resources and (len(resources) != 1 or resources[0] != provider.resource):
            return JSONResponse({"error": "invalid_target", "error_description": "Unsupported resource"},
                                status_code=400, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})
        return await handler.handle(request)

    replacements = {
        "/.well-known/oauth-authorization-server": (metadata_handler, ["GET", "OPTIONS"]),
        "/token": (token_handler, ["POST", "OPTIONS"]),
    }
    for index, route in enumerate(app.routes):
        if getattr(route, "path", None) in replacements:
            endpoint, methods = replacements[route.path]
            app.routes[index] = Route(route.path, endpoint=RequestBodyLimitMiddleware(
                cors_middleware(endpoint, methods), DEFAULT_MAX_REQUEST_BODY_SIZE), methods=methods)
    return app
