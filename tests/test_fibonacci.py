import unittest
from src.utils.fibonacci import fibonacci

class TestFibonacci(unittest.TestCase):
    def test_fibonacci_ten(self):
        """
        Verifica que fibonacci(10) devuelve la secuencia esperada: [0, 1, 1, 2, 3, 5, 8, 13, 21, 34].
        """
        expected = [0, 1, 1, 2, 3, 5, 8, 13, 21, 34]
        result = fibonacci(10)
        self.assertEqual(result, expected)

    def test_fibonacci_small_n(self):
        """Verifica casos base pequeños."""
        self.assertEqual(fibonacci(1), [0])
        self.assertEqual(fibonacci(2), [0, 1])
        self.assertEqual(fibonacci(3), [0, 1, 1])
        self.assertEqual(fibonacci(5), [0, 1, 1, 2, 3])

if __name__ == '__main__':
    unittest.main()
