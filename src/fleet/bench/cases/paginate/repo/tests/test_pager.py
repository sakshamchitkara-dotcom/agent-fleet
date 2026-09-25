import unittest

from pager import page, page_count


class PagerTest(unittest.TestCase):
    def test_full_page(self):
        self.assertEqual(page(list(range(25)), 0, 10), list(range(10)))

    def test_last_partial_page(self):
        self.assertEqual(page(list(range(25)), 2, 10), [20, 21, 22, 23, 24])

    def test_page_count(self):
        self.assertEqual(page_count(list(range(25)), 10), 3)

    def test_bad_args(self):
        with self.assertRaises(ValueError):
            page([], -1)
