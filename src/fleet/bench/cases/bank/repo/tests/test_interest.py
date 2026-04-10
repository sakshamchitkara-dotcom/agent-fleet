import unittest

from bank.interest import compound


class InterestTest(unittest.TestCase):
    def test_compound(self):
        self.assertEqual(compound(1000, 5, 2), 1102.5)

    def test_zero_years(self):
        self.assertEqual(compound(1000, 5, 0), 1000)
