from django.shortcuts import redirect, render


def landing(request):
    """
    Public home:
    - Main website shop selected → show that shop catalogue
    - Not selected → send visitors to the login page
    """
    from shops.services import get_main_website_shop
    from shops.views import shop_website

    main_shop = get_main_website_shop()
    if main_shop:
        return shop_website(request, main_shop.pk)
    return redirect("employees:login")


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
    from django.urls import reverse
    from pathlib import Path
    from shops.services import get_company_display_name

    manifest_path = Path(settings.BASE_DIR) / "static" / "manifest.webmanifest"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    brand = get_company_display_name()
    login_path = reverse("employees:login")
    data["name"] = brand
    data["short_name"] = brand if len(brand) <= 12 else brand[:11].rstrip() + "…"
    data["id"] = login_path
    data["start_url"] = f"{login_path}?source=pwa"
    data["scope"] = "/"
    data["display"] = "standalone"
    data["theme_color"] = "#0f8f82"
    data["background_color"] = "#061216"
    data["prefer_related_applications"] = False
    data["related_applications"] = [
        {
            "platform": "webapp",
            "url": request.build_absolute_uri(reverse("core:web_manifest")),
        }
    ]
    return JsonResponse(data, content_type="application/manifest+json")
