from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, TestCase
from django.urls import reverse


class WebManifestTests(TestCase):
    def test_manifest_starts_at_employee_login(self):
        response = self.client.get(reverse("core:web_manifest"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response["Content-Type"].split(";")[0],
            "application/manifest+json",
        )
        data = response.json()
        login_path = reverse("employees:login")
        self.assertEqual(data["start_url"], f"{login_path}?source=pwa")
        self.assertEqual(data["id"], login_path)
        self.assertEqual(data["display"], "standalone")
        self.assertTrue(any(icon.get("src", "").endswith("myshop-512.png") for icon in data["icons"]))


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
