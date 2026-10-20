import base64
import hashlib
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import httpx
from cryptography.fernet import Fernet
from mcp.server import MCPServer
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions

from travel_mcp.auth import TravelOAuthProvider, add_portal_routes, current_user_id, finalize_oauth_routes
from travel_mcp.store import Store


BASE = "https://travel.example"
CALLBACK = "workbuddy://workbuddy/mcp/connector%3Asoco-travel-assistant/oauth/callback"
VERIFIER = "a" * 64
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).decode().rstrip("=")


class AuthTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name), Fernet.generate_key().decode())
        self.alice = self.store.create_user("alice", "a-strong-password-123")
        self.bob = self.store.create_user("bob", "b-strong-password-123")
        self.settings = SimpleNamespace(public_base_url=BASE, allowed_redirect_origins=("https://approved.example",),
                                        access_token_seconds=3600, refresh_token_seconds=2592000,
                                        login_session_seconds=28800, cookie_secure=True)
        self.provider = TravelOAuthProvider(self.store, self.settings)
        self.app = self.make_app(self.provider)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url=BASE,
                                       headers={"Origin": BASE}, follow_redirects=False)

    def make_app(self, provider):
        server = MCPServer("test", auth_server_provider=provider, auth=AuthSettings(
            issuer_url=BASE, resource_server_url=BASE + "/mcp", validate_token_resource=True,
            required_scopes=["travel"], client_registration_options=ClientRegistrationOptions(
                enabled=True, valid_scopes=["travel"], default_scopes=["travel"])))
        add_portal_routes(server, provider, self.store, self.settings)
        app = server.streamable_http_app(stateless_http=True, json_response=True, host="travel.example")
        finalize_oauth_routes(app, provider)
        return app

    async def asyncTearDown(self):
        await self.client.aclose()
        self.temp.cleanup()

    @staticmethod
    def csrf(response):
        found = re.search(r'name="csrf" value="([^"]+)"', response.text)
        if not found:
            raise AssertionError("page has no CSRF field: " + response.text[:150])
        return found.group(1)

    async def register(self, redirect=CALLBACK):
        return await self.client.post("/register", json={"redirect_uris": [redirect],
            "token_endpoint_auth_method": "none", "client_name": "WorkBuddy", "scope": "travel"})

    async def login(self):
        page = await self.client.get("/account")
        result = await self.client.post("/account/login", data={"csrf": self.csrf(page),
                "username": "alice", "password": "a-strong-password-123"})
        self.assertEqual(result.status_code, 303, result.text)

    async def code(self, client_id):
        result = await self.client.get("/authorize", params={"response_type": "code", "client_id": client_id,
            "redirect_uri": CALLBACK, "scope": "travel", "code_challenge": CHALLENGE,
            "code_challenge_method": "S256", "state": "state123", "resource": BASE + "/mcp"})
        self.assertEqual(result.status_code, 302, result.text)
        flow = parse_qs(urlparse(result.headers["location"]).query)["flow"][0]
        page = await self.client.get(result.headers["location"])
        self.assertIn("WorkBuddy", page.text)
        approved = await self.client.post("/account/authorize", data={"flow": flow,
            "csrf": self.csrf(page), "decision": "approve", "user_id": self.bob["id"]})
        self.assertEqual(approved.status_code, 303, approved.text)
        query = parse_qs(urlparse(approved.headers["location"]).query)
        self.assertEqual(query["state"], ["state123"])
        return query["code"][0]

    async def redeem(self, client_id, code, verifier=VERIFIER, resource=BASE + "/mcp"):
        return await self.client.post("/token", data={"grant_type": "authorization_code", "client_id": client_id,
            "code": code, "redirect_uri": CALLBACK, "code_verifier": verifier, "resource": resource})

    async def test_registration_metadata_and_redirect_restrictions(self):
        metadata = await self.client.get("/.well-known/oauth-authorization-server")
        self.assertIn("none", metadata.json()["token_endpoint_auth_methods_supported"])
        self.assertEqual(metadata.json()["issuer"], BASE)
        good = await self.register()
        self.assertEqual(good.status_code, 201, good.text)
        self.assertEqual(good.json()["redirect_uris"], [CALLBACK])
        for redirect in ["https://evil.example/oauth/callback", "http://example.com/oauth/callback",
                         "http://127.0.0.1:8888/other", "workbuddy://workbuddy/other",
                         "https://approved.example@evil.example/oauth/callback"]:
            with self.subTest(redirect=redirect):
                self.assertEqual((await self.register(redirect)).status_code, 400)
        for redirect in ["http://127.0.0.1:8888/oauth/callback", "http://[::1]:8888/oauth/callback",
                         "https://approved.example/oauth/callback"]:
            self.assertEqual((await self.register(redirect)).status_code, 201)
        self.assertEqual((await self.client.post("/mcp", json={})).status_code, 401)
        with self.assertRaises(PermissionError):
            current_user_id()

    async def test_pkce_replay_refresh_rotation_and_subject_survive_recreation(self):
        registered = (await self.register()).json()
        client_id = registered["client_id"]
        other = (await self.register()).json()["client_id"]
        await self.login()
        code = await self.code(client_id)
        self.assertEqual((await self.redeem(client_id, code, "wrong")).json()["error"], "invalid_grant")
        self.assertEqual((await self.redeem(other, code)).json()["error"], "invalid_grant")
        self.assertEqual((await self.redeem(client_id, code, resource="https://evil.example/mcp")).json()["error"], "invalid_target")
        issued = await self.redeem(client_id, code)
        self.assertEqual(issued.status_code, 200, issued.text)
        tokens = issued.json()
        self.assertEqual((await self.redeem(client_id, code)).json()["error"], "invalid_grant")
        restarted = TravelOAuthProvider(self.store, self.settings)
        access = await restarted.load_access_token(tokens["access_token"])
        self.assertEqual(access.subject, self.alice["id"])
        self.assertEqual(access.resource, BASE + "/mcp")
        self.app = self.make_app(restarted)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url=BASE) as client:
            wrong = await client.post("/token", data={"grant_type": "refresh_token", "client_id": other,
                "refresh_token": tokens["refresh_token"]})
            self.assertEqual(wrong.json()["error"], "invalid_grant")
            result = await client.post("/token", data={"grant_type": "refresh_token", "client_id": client_id,
                "refresh_token": tokens["refresh_token"]})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertNotEqual(result.json()["refresh_token"], tokens["refresh_token"])
            replay = await client.post("/token", data={"grant_type": "refresh_token", "client_id": client_id,
                "refresh_token": tokens["refresh_token"]})
            self.assertEqual(replay.json()["error"], "invalid_grant")
        self.assertIsNone(await restarted.load_access_token(tokens["access_token"]))
        with self.store.connection() as conn:
            rows = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name LIKE 'oauth_%'").fetchall()
            self.assertTrue(rows)
            for row in rows:
                name = re.search(r'CREATE TABLE (?:IF NOT EXISTS )?(\w+)', row[0], re.I).group(1)
                dumped = str([tuple(x) for x in conn.execute("SELECT * FROM " + name)])
                self.assertNotIn(tokens["access_token"], dumped)
                self.assertNotIn(tokens["refresh_token"], dumped)

    async def test_portal_csrf_secrets_and_user_isolation(self):
        initial = await self.client.get("/account")
        self.assertIn("httponly", initial.headers["set-cookie"].lower())
        self.assertIn("secure", initial.headers["set-cookie"].lower())
        no_csrf = await self.client.post("/account/login", data={"username": "alice", "password": "a-strong-password-123"})
        self.assertEqual(no_csrf.status_code, 403)
        await self.login()
        page = await self.client.get("/account")
        data = {"csrf": self.csrf(page), "provider": "tencentmap", "key": "TOP-SECRET-KEY", "user_id": self.bob["id"]}
        self.assertEqual((await self.client.post("/account/credentials", data={**data, "csrf": "wrong"})).status_code, 403)
        self.assertEqual((await self.client.post("/account/credentials", data=data, headers={"Origin": "https://evil.example"})).status_code, 403)
        self.assertEqual((await self.client.post("/account/credentials", data={**data, "csrf": "错误"})).status_code, 403)
        saved = await self.client.post("/account/credentials", data=data)
        self.assertEqual(saved.status_code, 303, saved.text)
        self.assertNotIn("TOP-SECRET-KEY", saved.text + str(saved.headers))
        account = await self.client.get("/account")
        self.assertNotIn("TOP-SECRET-KEY", account.text)
        self.assertRegex(account.text, r'type="password"[^>]*value=""')
        self.assertIsNotNone(self.store.get_credentials(self.alice["id"], "tencentmap"))
        self.assertIsNone(self.store.get_credentials(self.bob["id"], "tencentmap"))
        logged_out = await self.client.post("/account/logout", data={"csrf": self.csrf(account)})
        self.assertEqual(logged_out.status_code, 303)
        denied = await self.client.post("/account/credentials", data=data)
        self.assertIn(denied.status_code, [401, 403])

    async def test_consent_needs_session_csrf_and_explicit_approval(self):
        client_id = (await self.register()).json()["client_id"]
        await self.login()
        auth = await self.client.get("/authorize", params={"response_type": "code", "client_id": client_id,
            "redirect_uri": CALLBACK, "code_challenge": CHALLENGE, "resource": BASE + "/mcp"})
        page = await self.client.get(auth.headers["location"])
        flow = parse_qs(urlparse(auth.headers["location"]).query)["flow"][0]
        self.assertEqual((await self.client.post("/account/authorize", data={"flow": flow, "decision": "approve"})).status_code, 403)
        missing = await self.client.post("/account/authorize", data={"flow": flow, "csrf": self.csrf(page)})
        self.assertEqual(missing.status_code, 400)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url=BASE) as stranger:
            copied = await stranger.get(auth.headers["location"])
            self.assertEqual(copied.status_code, 400)

    async def test_authorization_login_rotates_session_and_grants_verified_subject(self):
        from mcp.server.auth.middleware.auth_context import auth_context_var
        from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
        client_id = (await self.register()).json()["client_id"]
        auth = await self.client.get("/authorize", params={"response_type": "code", "client_id": client_id,
            "redirect_uri": CALLBACK, "code_challenge": CHALLENGE})
        page = await self.client.get(auth.headers["location"])
        before = self.client.cookies.get("travel_session")
        flow = parse_qs(urlparse(auth.headers["location"]).query)["flow"][0]
        login = await self.client.post("/account/login", data={"csrf": self.csrf(page), "flow": flow,
            "username": "alice", "password": "a-strong-password-123"})
        self.assertEqual(login.status_code, 303, login.text)
        self.assertNotEqual(before, self.client.cookies.get("travel_session"))
        consent = await self.client.get(login.headers["location"])
        decision = await self.client.post("/account/authorize", data={"flow": flow,
            "csrf": self.csrf(consent), "decision": "approve"})
        self.assertEqual(decision.status_code, 303, decision.text)
        code = parse_qs(urlparse(decision.headers["location"]).query)["code"][0]
        token = (await self.redeem(client_id, code)).json()["access_token"]
        verified = await self.provider.load_access_token(token)
        marker = auth_context_var.set(AuthenticatedUser(verified))
        try:
            self.assertEqual(current_user_id(), self.alice["id"])
        finally:
            auth_context_var.reset(marker)
        with self.store.connection() as conn:
            conn.execute("UPDATE oauth_tokens SET expires_at=0")
        self.assertIsNone(await self.provider.load_access_token(token))

    async def test_login_attempts_are_limited(self):
        page = await self.client.get("/account")
        for _ in range(5):
            result = await self.client.post("/account/login", data={"csrf": self.csrf(page),
                "username": "alice", "password": "incorrect"})
            self.assertEqual(result.status_code, 401)
        limited = await self.client.post("/account/login", data={"csrf": self.csrf(page),
            "username": "alice", "password": "incorrect"})
        self.assertEqual(limited.status_code, 429)


if __name__ == "__main__":
    unittest.main()
