from __future__ import annotations

import time
from dataclasses import dataclass, field
from collections import defaultdict


@dataclass
class RateLimiter:
    requests_per_second: int = 2
    burst_multiplier: int = 2    # Hard limit: requests * burst_multiplier
    sleep_seconds: float = 1.0   # Backpressure sleep duration
    
    # Stores timestamps of recent requests per client_key
    _history: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))
    
    def check(self, client_key: str) -> str:
        """
        Check if a request from client_key should be allowed, throttled, or rejected.
        Returns: 'allow', 'throttle', or 'reject'
        """
        now = time.time()
        self._clean_client_history(client_key, now)
        
        history = self._history[client_key]
        count = len(history)
        
        # Hard limit check
        if count >= self.requests_per_second * self.burst_multiplier:
            return "reject"
            
        # Throttling check
        if count >= self.requests_per_second:
            history.append(now)
            return "throttle"
            
        # Allow
        history.append(now)
        return "allow"

    def _clean_client_history(self, client_key: str, now: float) -> None:
        """Remove requests older than 1 second for a specific client."""
        cutoff = now - 1.0
        self._history[client_key] = [ts for ts in self._history[client_key] if ts > cutoff]

    def cleanup_all(self) -> None:
        """Remove all history entries older than 1 second to prevent memory leaks."""
        now = time.time()
        cutoff = now - 1.0
        keys_to_delete = []
        for key, history in self._history.items():
            new_history = [ts for ts in history if ts > cutoff]
            if not new_history:
                keys_to_delete.append(key)
            else:
                self._history[key] = new_history
        
        for key in keys_to_delete:
            del self._history[key]
