from django.test import SimpleTestCase

from shops.services import client_phone_for_receipt, mask_client_phone_for_print


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


class ClientPhoneForReceiptTests(SimpleTestCase):
    def test_show_keeps_the_full_number(self):
        self.assertEqual(
            client_phone_for_receipt("+254795606115", show_full=True),
            "+254795606115",
        )

    def test_hide_keeps_the_first_seven_digits_and_masks_the_rest(self):
        self.assertEqual(
            client_phone_for_receipt("0712345678", show_full=False),
            "0712345***",
        )
        self.assertEqual(
            client_phone_for_receipt("+254 712 555 010", show_full=False),
            "+254 712 5** ***",
        )
        self.assertEqual(
            client_phone_for_receipt("+254795606115", show_full=False),
            "+2547956*****",
        )
