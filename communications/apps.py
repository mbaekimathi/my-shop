from django.apps import AppConfig


class CommunicationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "communications"
    verbose_name = "WhatsApp"

    def ready(self):
        # Auto-start local WhatsApp helper with the web app (VPS / same-server).
        try:
            from .launcher import maybe_autostart_in_background

            maybe_autostart_in_background()
        except Exception:
            # Never block Django boot if Node/Chromium is missing.
            pass
