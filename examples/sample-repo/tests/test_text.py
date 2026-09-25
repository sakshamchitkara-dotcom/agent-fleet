import unittest

from shop.text import slugify


class SlugifyTest(unittest.TestCase):
    def test_simple(self):
        self.assertEqual(slugify("Blue Hat"), "blue-hat")

    def test_collapses_and_strips(self):
        self.assertEqual(slugify("  Red  Shoes!! "), "red-shoes")
