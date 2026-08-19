from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class DarkModeStylesTests(SimpleTestCase):
    def test_core_css_keeps_native_controls_readable_in_dark_mode(self):
        css = (Path(settings.BASE_DIR) / "static" / "css" / "core.css").read_text(
            encoding="utf-8"
        )
        self.assertIn("color-scheme: dark", css)
        self.assertIn("color: CanvasText", css)
        self.assertIn("appearance: base-select", css)
        self.assertIn("select::picker(select)", css)
        self.assertIn("input:-webkit-autofill", css)
        self.assertIn(".field select", css)

    def test_app_css_styles_share_filters_and_custom_select_chevrons(self):
        css = (Path(settings.BASE_DIR) / "static" / "css" / "app.css").read_text(
            encoding="utf-8"
        )
        self.assertIn(".staff-select select::picker-icon", css)
        self.assertIn(".hr-approve-select select::picker-icon", css)
        self.assertIn(".wa-auto__field select", css)
        self.assertRegex(
            css,
            r"\.wa-person__meta em\s*\{[^}]*color:\s*var\(--text\)",
        )
