from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from shops.services import _parse_expense_lines


def payload(category="rent", name="Shop rent", amount="1000"):
    return {
        "category": [category],
        "name": [name],
        "amount": [amount],
    }


class ParseExpenseLinesTests(SimpleTestCase):
    def test_preset_slug_kept(self):
        lines = _parse_expense_lines(payload(category="rent"))
        self.assertEqual(lines[0]["category"], "rent")

    def test_preset_label_maps_to_slug(self):
        lines = _parse_expense_lines(payload(category="Office supplies"))
        self.assertEqual(lines[0]["category"], "office")

    def test_preset_label_with_separator_maps_to_slug(self):
        lines = _parse_expense_lines(payload(category="Food & refreshments"))
        self.assertEqual(lines[0]["category"], "food")

    def test_custom_category_stored_uppercase(self):
        lines = _parse_expense_lines(payload(category="Grocery"))
        self.assertEqual(lines[0]["category"], "GROCERY")

    def test_custom_category_normalizes_whitespace(self):
        lines = _parse_expense_lines(payload(category="  fuel   &  lubricants "))
        self.assertEqual(lines[0]["category"], "FUEL & LUBRICANTS")

    def test_empty_category_rejected(self):
        with self.assertRaises(ValidationError):
            _parse_expense_lines(payload(category=""))

    def test_symbol_only_category_rejected(self):
        with self.assertRaises(ValidationError):
            _parse_expense_lines(payload(category="---"))

    def test_too_long_category_rejected(self):
        with self.assertRaises(ValidationError):
            _parse_expense_lines(payload(category="X" * 33))

    def test_owner_drawings_blocked(self):
        for value in ("owner_drawings", "Owner Drawings", "OWNER-DRAWINGS"):
            with self.assertRaises(ValidationError, msg=value):
                _parse_expense_lines(payload(category=value))

    def test_multiple_lines(self):
        lines = _parse_expense_lines(
            {
                "category": ["rent", "Grocery"],
                "name": ["Rent", "Stock"],
                "amount": ["500", "250"],
            }
        )
        self.assertEqual(
            [(line["category"], line["amount"]) for line in lines],
            [("rent", Decimal("500")), ("GROCERY", Decimal("250"))],
        )
