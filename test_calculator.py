#!/usr/bin/env python
import unittest
from calculator import add, subtract, multiply, divide, power

class TestCalculator(unittest.TestCase):
    def test_add(self):
        self.assertEqual(add(1, 2), 3)
        self.assertEqual(add(-1, -1), -2)
    
    def test_subtract(self):
        self.assertEqual(subtract(10, 5), 5)
        self.assertEqual(subtract(-1, -1), 0)
    
    def test_multiply(self):
        self.assertEqual(multiply(3, 4), 12)
        self.assertEqual(multiply(-1, 1), -1)
    
    def test_divide(self):
        self.assertEqual(divide(8, 4), 2)
        with self.assertRaises(ValueError):
            divide(5, 0)
    
    def test_power(self):
        self.assertEqual(power(2, 3), 8)
        self.assertEqual(power(-1, 2), 1)
        self.assertEqual(power(5, 0), 1)