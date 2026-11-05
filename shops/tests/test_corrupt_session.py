"""Unreadable session rows are discarded instead of warning on every request."""

from django.conf import settings
from django.contrib.sessions.models import Session
from django.test import Client, TestCase, override_settings

from myshop.session_store.cached_db import SessionStore as CachedSessionStore
from myshop.session_store.db import SessionStore


def _corrupt(store):
    store["keep"] = "yes"
    store.save()
    key = store.session_key
    Session.objects.filter(session_key=key).update(session_data="not-a-signed-session")
    return key


class CorruptSessionTests(TestCase):
    def test_database_store_drops_unreadable_row(self):
        healthy = SessionStore()
        healthy["keep"] = "yes"
        healthy.save()
        self.assertEqual(SessionStore(healthy.session_key).load()["keep"], "yes")

        key = _corrupt(SessionStore())
        with self.assertNoLogs("django.security.SuspiciousSession", level="WARNING"):
            loaded = SessionStore(key)
            self.assertEqual(loaded.load(), {})
        self.assertIsNone(loaded.session_key)
        self.assertFalse(Session.objects.filter(session_key=key).exists())

    def test_cached_store_drops_unreadable_row(self):
        # Sign with the DB store so the cache does not still hold the good payload.
        key = _corrupt(SessionStore())
        with self.assertNoLogs("django.security.SuspiciousSession", level="WARNING"):
            loaded = CachedSessionStore(key)
            self.assertEqual(loaded.load(), {})
        self.assertIsNone(loaded.session_key)
        self.assertFalse(Session.objects.filter(session_key=key).exists())

    @override_settings(
        SESSION_ENGINE="myshop.session_store.db",
        ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"],
    )
    def test_request_clears_the_bad_cookie(self):
        key = _corrupt(SessionStore())
        client = Client()
        client.cookies[settings.SESSION_COOKIE_NAME] = key
        with self.assertNoLogs("django.security.SuspiciousSession", level="WARNING"):
            response = client.get("/employees/api/ping/")
        self.assertEqual(response.status_code, 200)
        cookie = response.cookies.get(settings.SESSION_COOKIE_NAME)
        self.assertIsNotNone(cookie)
        self.assertEqual(cookie.value, "")
        self.assertFalse(Session.objects.filter(session_key=key).exists())
