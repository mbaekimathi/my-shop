"""Public client credit note page and self-service M-Pesa payment."""

from django.core.exceptions import ValidationError
from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from shops.credit_note import (
    apply_client_credit_note_payment,
    build_client_credit_note_account,
    credit_note_branding,
    unsign_client_credit_token,
)
from shops.daraja_stk import (
    get_stk_payment,
    initiate_stk_push,
    stk_payment_payload,
    stk_ready,
    sync_callback_base_from_request,
)
from shops.models import Client
from shops.services import get_daraja_settings


def _client_from_token(token: str) -> Client:
    client_id = unsign_client_credit_token(token)
    client = Client.objects.filter(pk=client_id).first()
    if client is None:
        raise Http404("Client not found.")
    return client


def _stk_belongs_to_client(payment, client_id: int) -> bool:
    if payment is None:
        return False
    if payment.account_kind and payment.account_kind != "credit":
        return False
    if payment.account_id and int(payment.account_id) != int(client_id):
        return False
    return True


@require_GET
def credit_note_page(request, token):
    client = _client_from_token(token)
    account = build_client_credit_note_account(client_id=client.pk)
    branding = credit_note_branding(request=request)
    stk_settings = get_daraja_settings()
    from django.urls import reverse

    status_template = reverse(
        "core:credit_note_stk_status",
        kwargs={"token": token, "payment_id": "00000000-0000-0000-0000-000000000000"},
    ).replace("00000000-0000-0000-0000-000000000000", "__ID__")
    return render(
        request,
        "shops/credit_note.html",
        {
            "token": token,
            "client_phone": client.phone_number,
            "stk_ready": stk_ready(),
            "stk_off_label": stk_settings.stk_not_ready_reason() or "M-Pesa unavailable",
            "stk_initiate_url": reverse("core:credit_note_stk", kwargs={"token": token}),
            "stk_status_url_template": status_template,
            "pay_url": reverse("core:credit_note_pay", kwargs={"token": token}),
            **branding,
            **account,
        },
    )


@require_POST
def credit_note_stk_initiate(request, token):
    client = _client_from_token(token)
    sync_callback_base_from_request(request, persist=True)
    if not stk_ready():
        return JsonResponse(
            {"ok": False, "error": "M-Pesa payments are not available right now."},
            status=400,
        )
    phone = (request.POST.get("phone") or client.phone_number or "").strip()
    if not phone:
        return JsonResponse(
            {"ok": False, "error": "Enter your M-Pesa phone number."},
            status=400,
        )
    try:
        payment = initiate_stk_push(
            purpose="credit",
            amount=request.POST.get("amount"),
            phone=phone,
            account_reference=f"CR{client.pk}",
            description=f"Credit {client.full_name}"[:40],
            profile=None,
            account_kind="credit",
            account_id=client.pk,
            request=request,
        )
    except ValidationError as exc:
        message = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
        return JsonResponse({"ok": False, "error": message}, status=400)

    return JsonResponse(
        {
            "ok": True,
            "message": "Check your phone and enter your M-Pesa PIN to confirm.",
            **stk_payment_payload(payment),
        }
    )


@require_GET
def credit_note_stk_status(request, token, payment_id):
    client = _client_from_token(token)
    payment = get_stk_payment(payment_id)
    if not _stk_belongs_to_client(payment, client.pk):
        return JsonResponse({"ok": False, "error": "Payment not found."}, status=404)
    return JsonResponse({"ok": True, **stk_payment_payload(payment)})


@require_POST
def credit_note_pay(request, token):
    client = _client_from_token(token)
    try:
        result = apply_client_credit_note_payment(
            client_id=client.pk,
            amount=request.POST.get("amount"),
            phone=(request.POST.get("phone") or client.phone_number or "").strip(),
            stk_payment_id=(request.POST.get("stk_payment_id") or "").strip(),
        )
    except ValidationError as exc:
        message = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
        return JsonResponse({"ok": False, "error": message}, status=400)
    return JsonResponse(result)
