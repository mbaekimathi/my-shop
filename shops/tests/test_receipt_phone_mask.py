from django.test import SimpleTestCase

from shops.services import mask_client_phone_for_print


class MaskClientPhoneForPrintTests(SimpleTestCase):
    def test_hides_last_six_digits_and_keeps_spacing(self):
        self.assertEqual(mask_client_phone_for_print("0712345678"), "0712******")
        self.assertEqual(
            mask_client_phone_for_print("+254 712 555 010"),
            "+254 712 *** ***",
        )

    def test_blank_phone_stays_blank(self):
        self.assertEqual(mask_client_phone_for_print(""), "")
        self.assertEqual(mask_client_phone_for_print("   "), "   ")
