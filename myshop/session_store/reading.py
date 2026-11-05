"""Decode session rows and drop ones signed with a retired secret."""

from django.core import signing


def load_signed_session(store, session_data):
    """Return the decoded session, or an empty one when the row is unreadable.

    Django's own decoder logs ``Session data corrupted`` and leaves the row
    in place, so the same cookie warns on every later request. A signature
    mismatch means the secret changed (or the row was truncated). The data
    cannot be recovered, so delete it and clear the key. The response then
    drops the browser cookie instead of sending the bad id again.
    """
    try:
        return signing.loads(
            session_data,
            salt=store.key_salt,
            serializer=store.serializer,
        )
    except Exception:
        key = store.session_key
        if key:
            try:
                store.delete(key)
            except Exception:
                pass
            store._session_key = None
        return {}
