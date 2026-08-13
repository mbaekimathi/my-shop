from django.shortcuts import render


def landing(request):
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
