import unittest
from calculator import average


class AverageTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(average([]), 0)

    def test_positive(self):
        self.assertEqual(average([2, 4]), 3)

    def test_negative(self):
        self.assertEqual(average([-2, -4]), -3)
