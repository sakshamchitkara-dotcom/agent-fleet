import unittest

from duration import parse_duration


class DurationTest(unittest.TestCase):
    def test_hours_minutes(self):
        self.assertEqual(parse_duration("1h30m"), 5400)

    def test_seconds(self):
        self.assertEqual(parse_duration(" 45s "), 45)

    def test_minutes_seconds(self):
        self.assertEqual(parse_duration("2m5s"), 125)

    def test_garbage(self):
        with self.assertRaises(ValueError):
            parse_duration("1x")
