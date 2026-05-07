import unittest
import time
from nemo_coding_platform.core.rate_limiter import RateLimiter


class TestRateLimiter(unittest.TestCase):
    def test_allow_normal(self):
        limiter = RateLimiter(requests_per_second=10, burst_multiplier=2)
        for _ in range(5):
            self.assertEqual(limiter.check("client1"), "allow")

    def test_throttle(self):
        # 1 request per second, burst of 2
        limiter = RateLimiter(requests_per_second=1, burst_multiplier=2)
        self.assertEqual(limiter.check("client1"), "allow")
        # Second request in the same second should throttle
        self.assertEqual(limiter.check("client1"), "throttle")

    def test_reject(self):
        # 1 request per second, burst of 2
        limiter = RateLimiter(requests_per_second=1, burst_multiplier=2)
        limiter.check("client1") # allow
        limiter.check("client1") # throttle
        # Third request should reject
        self.assertEqual(limiter.check("client1"), "reject")

    def test_cleanup_and_recovery(self):
        limiter = RateLimiter(requests_per_second=10, burst_multiplier=2)
        limiter.check("client1")
        time.sleep(1.1)
        # After 1 second, it should allow again
        self.assertEqual(limiter.check("client1"), "allow")

    def test_cleanup_all(self):
        limiter = RateLimiter(requests_per_second=10, burst_multiplier=2)
        limiter.check("client1")
        limiter.check("client2")
        self.assertEqual(len(limiter._history), 2)
        time.sleep(1.1)
        limiter.cleanup_all()
        self.assertEqual(len(limiter._history), 0)


if __name__ == "__main__":
    unittest.main()
