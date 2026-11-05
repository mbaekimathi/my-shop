"""Database session store that drops unreadable rows."""

from django.contrib.sessions.backends.db import SessionStore as DjangoDBStore

from .reading import load_signed_session


class SessionStore(DjangoDBStore):
    def load(self):
        row = self._get_session_from_db()
        if row is None:
            return {}
        return load_signed_session(self, row.session_data)
