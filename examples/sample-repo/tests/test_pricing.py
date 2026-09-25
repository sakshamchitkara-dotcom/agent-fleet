import unittest

from shop.pricing import apply_discount, cart_total


class PricingTest(unittest.TestCase):
    def test_no_discount(self):
        self.assertEqual(apply_discount(80.0, 0), 80.0)

    def test_discount(self):
        self.assertEqual(apply_discount(80.0, 25), 60.0)

    def test_cart_total(self):
        self.assertEqual(cart_total([(10.0, 2), (5.5, 2)], discount=10), 27.9)

    def test_invalid_percent(self):
        with self.assertRaises(ValueError):
            apply_discount(10, 150)
