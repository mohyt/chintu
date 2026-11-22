import unittest

from chintu_gateway import ataria_web
from test_ataria import TOKEN, AtariaCase, FakeEngine


class DashboardTests(AtariaCase):
    """The dashboard in a browser: one-time link, session cookie, pages passed to the engine."""

    def link(self, client=None, headers=None, app=None):
        return self.call("POST", "/api/v1/ataria/link", schema="AtariaDashboardLink",
                         client=client, headers=headers, app=app)

    def open_dashboard(self, client=None, headers=None, app=None):
        client = client or self.client
        path = self.link(client, headers, app)["path"]
        response = client.get(path, follow_redirects=False)
        self.assertEqual((response.status_code, response.headers["location"]), (303, "/ataria/"))
        return response

    def test_link_cookie_and_pages(self):
        response = self.open_dashboard()
        cookie = response.headers["set-cookie"]
        for part in ("chintu_ataria=", "HttpOnly", "SameSite=strict", "Path=/ataria/", "Max-Age=43200", "Secure"):
            self.assertIn(part, cookie)
        page = self.client.get("/ataria/")
        self.assertEqual(page.status_code, 200, page.text)
        self.assertIn("<title>Ataria</title>", page.text)
        self.assertEqual(page.headers["x-frame-options"], "DENY")
        self.assertIn("frame-ancestors 'none'", page.headers["content-security-policy"])
        method, path, headers = FakeEngine.seen[-1]
        self.assertEqual((method, path, headers["Authorization"]), ("GET", "/ataria/", "Bearer " + TOKEN))
        self.assertEqual(self.client.get("/ataria/api/today?date=2026-11-21").json(), {"frontmatter": {"date": "2026-11-21"}})
        # An unknown content type is never served as itself.
        self.assertEqual(self.client.get("/ataria/odd").headers["content-type"], "application/octet-stream")
        self.assertEqual(self.client.get("/ataria", follow_redirects=False).headers["location"], "/ataria/")

    def test_a_code_works_once(self):
        path = self.link()["path"]
        self.assertEqual(self.client.get(path, follow_redirects=False).status_code, 303)
        self.client.cookies.clear()
        self.assertEqual(self.client.get(path, follow_redirects=False).status_code, 401)
        self.assertEqual(self.client.get("/ataria/open?code=nonsense").status_code, 401)

    def test_no_session_no_pages(self):
        response = self.client.get("/ataria/")
        self.assertEqual(response.status_code, 401)
        self.assertIn("Chintu app", response.text)
        self.client.cookies.set("chintu_ataria", "made-up", path="/ataria/")
        self.assertEqual(self.client.get("/ataria/").status_code, 401)
        self.assertFalse(FakeEngine.seen)

    def test_writes_need_the_header(self):
        self.open_dashboard()
        refused = self.client.post("/ataria/api/done", json={"uid": "work.a"})
        self.assertEqual(refused.status_code, 403)
        accepted = self.client.post("/ataria/api/done", json={"uid": "work.a"}, headers={"X-Ataria": "1"})
        self.assertEqual((accepted.status_code, accepted.json()), (200, {"ok": True}))
        self.assertEqual(FakeEngine.posted, {"uid": "work.a"})

    def test_paths_cannot_leave_the_web_root(self):
        self.open_dashboard()
        for path in ("/ataria/../etc/passwd", "/ataria/%2e%2e/x", "/ataria/a//b", "/ataria/.hidden"):
            self.assertIn(self.client.get(path).status_code, (404,), path)
        for value in ("..", "a/../b", "a//b", ".x/y", "a/%2f"):
            self.assertIsNone(ataria_web.web_path(value), value)
        self.assertEqual(ataria_web.web_path("api/today"), "api/today")
        self.assertEqual(ataria_web.web_path("assets/"), "assets/")

    def test_revoking_the_device_ends_its_session(self):
        self.open_dashboard()
        self.assertEqual(self.client.get("/ataria/").status_code, 200)
        self.client.delete("/api/v1/clients/me", headers=self.headers)
        self.assertEqual(self.client.get("/ataria/").status_code, 401)

    def test_tray_link_on_this_machine(self):
        from starlette.testclient import TestClient
        local = TestClient(self.app, client=("127.0.0.1", 50000))
        proof = {"X-Chintu-Local": self.app.state.local_secret, "Host": "127.0.0.1"}
        self.assertEqual(local.post("/local/ataria/link", headers={"Host": "127.0.0.1"}).status_code, 403)
        path = local.post("/local/ataria/link", headers=proof).json()["path"]
        response = local.get(path, headers={"Host": "127.0.0.1"}, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertNotIn("Secure", response.headers["set-cookie"])
        self.assertEqual(local.get("/ataria/", headers={"Host": "127.0.0.1"}).status_code, 200)
        self.assertTrue(local.get("/local/status", headers=proof).json()["ataria"])

    def test_not_configured(self):
        client, headers, app = self.start(None, "plain")
        self.call("POST", "/api/v1/ataria/link", 503, client=client, headers=headers, app=app)
        self.assertEqual(client.get("/ataria/").status_code, 503)

    def test_sessions_expire(self):
        now = [1000.0]
        sessions = ataria_web.Sessions(clock=lambda: now[0])
        code = sessions.link("c1")["path"].split("code=")[1]
        now[0] += ataria_web.CODE_SECONDS + 1
        self.assertIsNone(sessions.open(code))
        code = sessions.link("c1")["path"].split("code=")[1]
        token = sessions.open(code)
        self.assertEqual(sessions.owner(token), "c1")
        now[0] += ataria_web.SESSION_SECONDS + 1
        self.assertIsNone(sessions.owner(token))


if __name__ == "__main__":
    unittest.main()
