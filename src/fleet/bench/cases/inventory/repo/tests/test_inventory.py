import unittest

from inventory import add_item, total


class InventoryTest(unittest.TestCase):
    def test_fresh_stock_each_call(self):
        a = add_item("apple", 2)
        b = add_item("pear", 1)
        self.assertEqual(a, {"apple": 2})
        self.assertEqual(b, {"pear": 1})

    def test_existing_stock_updated(self):
        stock = {"apple": 1}
        self.assertIs(add_item("apple", 2, stock), stock)
        self.assertEqual(stock, {"apple": 3})

    def test_total(self):
        self.assertEqual(total({"a": 2, "b": 3}), 5)
