import sys

from django.apps import AppConfig


class ShopsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "shops"

    def ready(self):
        # Preload qrcode so the first POS settings / receipt page is not cold.
        try:
            import qrcode  # noqa: F401
        except Exception:
            pass

        # Skip cache warm during management commands that only need schema/URL checks.
        if any(cmd in sys.argv for cmd in ("check", "migrate", "makemigrations", "test")):
            return

        # Defer POS settings cache warm until DB connections are usable.
        from django.db.backends.signals import connection_created

        def _warm_pos_cache(sender=None, connection=None, **kwargs):
            if connection is not None and getattr(connection, "alias", "default") != "default":
                return
            if getattr(connection_created, "_myshop_pos_warmed", False):
                return
            try:
                from shops.services import get_company_pos_settings

                get_company_pos_settings()
                connection_created._myshop_pos_warmed = True
            except Exception:
                pass

        connection_created.connect(_warm_pos_cache, weak=False)