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
    from django.conf import settings
    from django.http import FileResponse
    from pathlib import Path

    manifest_path = Path(settings.BASE_DIR) / "static" / "manifest.webmanifest"
    return FileResponse(manifest_path.open("rb"), content_type="application/manifest+json")
