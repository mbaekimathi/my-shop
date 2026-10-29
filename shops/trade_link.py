"""Signed public links for a client's trade-in / trade-out account.

Each link carries a signed client id — a customer can only open the trade
account the link was issued for, so one client cannot view another's
transactions by guessing ids.
"""

from __future__ import annotations

import base64

from django.core.signing import BadSignature, Signer
from django.http import Http404
from django.urls import reverse

_TRADE_SIGNER = Signer(salt="myshop-client-trade-account-v1")


def sign_client_trade_token(client_id: int) -> str:
    raw = _TRADE_SIGNER.sign(str(int(client_id)))
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def unsign_client_trade_token(token: str) -> int:
    value = (token or "").strip()
    if not value:
        raise Http404("This link is invalid.")
    pad = "=" * (-len(value) % 4)
    try:
        raw = base64.urlsafe_b64decode(f"{value}{pad}").decode("utf-8")
        client_id = int(_TRADE_SIGNER.unsign(raw))
    except (BadSignature, ValueError, UnicodeDecodeError) as exc:
        raise Http404("This link is invalid or expired.") from exc
    if client_id <= 0:
        raise Http404("This link is invalid.")
    return client_id


def client_trade_url(client_id: int, *, request=None) -> str:
    from shops.credit_note import public_site_origin

    path = reverse(
        "core:trade_note",
        kwargs={"token": sign_client_trade_token(client_id)},
    )
    origin = public_site_origin(request=request)
    if origin:
        return f"{origin}{path}"
    if request is not None:
        absolute = request.build_absolute_uri(path)
        if (absolute or "").lower().startswith(("http://", "https://")):
            return absolute
    return ""
