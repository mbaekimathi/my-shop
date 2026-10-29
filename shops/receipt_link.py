"""Signed public links for customer receipts and quotations.

Each link carries a signed receipt id — a customer can only open the document
the link was issued for, so one client cannot view another's receipt or
quotation by guessing ids.
"""

from __future__ import annotations

import base64

from django.core.signing import BadSignature, Signer
from django.http import Http404
from django.urls import reverse

_DOC_SIGNER = Signer(salt="myshop-client-receipt-doc-v1")


def sign_receipt_doc_token(receipt_id: int) -> str:
    raw = _DOC_SIGNER.sign(str(int(receipt_id)))
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def unsign_receipt_doc_token(token: str) -> int:
    value = (token or "").strip()
    if not value:
        raise Http404("This link is invalid.")
    pad = "=" * (-len(value) % 4)
    try:
        raw = base64.urlsafe_b64decode(f"{value}{pad}").decode("utf-8")
        receipt_id = int(_DOC_SIGNER.unsign(raw))
    except (BadSignature, ValueError, UnicodeDecodeError) as exc:
        raise Http404("This link is invalid or expired.") from exc
    if receipt_id <= 0:
        raise Http404("This link is invalid.")
    return receipt_id


def client_receipt_doc_url(receipt_id: int, *, quotation: bool = False, request=None) -> str:
    from shops.credit_note import public_site_origin

    name = "core:quotation_doc" if quotation else "core:receipt_doc"
    path = reverse(name, kwargs={"token": sign_receipt_doc_token(receipt_id)})
    origin = public_site_origin(request=request)
    if origin:
        return f"{origin}{path}"
    if request is not None:
        absolute = request.build_absolute_uri(path)
        if (absolute or "").lower().startswith(("http://", "https://")):
            return absolute
    return ""
