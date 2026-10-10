from django.shortcuts import render


def landing(request):
    """
    Employee portal homepage on the app/subdomain.

    On the configured main (apex) domain, show the selected shop catalogue instead.
    """
    from shops.services import (
        get_company_profile,
        get_main_website_shop,
        main_website_host_matches,
    )
    from shops.views import shop_website

    company = get_company_profile()
    main_shop = get_main_website_shop()
    domain = getattr(company, "main_website_domain", "") or ""
    if main_shop and main_website_host_matches(request.get_host(), domain):
        return shop_website(request, main_shop.pk)
    return render(request, "core/landing.html")


def service_worker(request):
    from django.conf import settings
    from django.http import FileResponse
    from pathlib import Path

    sw_path = Path(settings.BASE_DIR) / "static" / "sw.js"
    response = FileResponse(sw_path.open("rb"), content_type="application/javascript")
    response["Service-Worker-Allowed"] = "/"
    response["Cache-Control"] = "no-cache"
    return response


def web_manifest(request):
    import json
    from django.conf import settings
    from django.http import JsonResponse
    from pathlib import Path
    from shops.services import get_company_display_name

    manifest_path = Path(settings.BASE_DIR) / "static" / "manifest.webmanifest"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    brand = get_company_display_name()
    data["name"] = brand
    data["short_name"] = brand
    return JsonResponse(data, content_type="application/manifest+json")
