"""Cached database session store that drops unreadable rows."""

from django.contrib.sessions.backends.cached_db import SessionStore as DjangoCachedStore

from .reading import load_signed_session


class SessionStore(DjangoCachedStore):
    def load(self):
        try:
            data = self._cache.get(self.cache_key)
        except Exception:
            # Some backends raise on an invalid cache key. Fall through to the DB.
            data = None

        if data is not None:
            return data

        row = self._get_session_from_db()
        if row is None:
            return {}
        expire_date = row.expire_date
        data = load_signed_session(self, row.session_data)
        if self.session_key:
            self._cache.set(
                self.cache_key,
                data,
                self.get_expiry_age(expiry=expire_date),
            )
        return data
