# tests/test_llm_sem.py
import threading
import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


def test_llm_sem_acquire_timeout_raises():
    """_llm_sem_acquire must raise RuntimeError when semaphore is held."""
    import nemo_coding_platform.mission_control_server as mcs

    acquired = threading.Event()
    release_flag = threading.Event()

    def holder():
        mcs._LLM_SEM.acquire()
        acquired.set()
        release_flag.wait(timeout=5)
        mcs._LLM_SEM.release()

    t = threading.Thread(target=holder, daemon=True)
    t.start()
    acquired.wait(timeout=2)

    with pytest.raises(RuntimeError, match="LLM semaphore busy"):
        mcs._llm_sem_acquire(timeout=0.5)

    release_flag.set()
    t.join(timeout=2)
    assert not t.is_alive(), "holder thread did not release semaphore within 2s"


def test_llm_sem_acquire_succeeds_when_free():
    """_llm_sem_acquire returns when semaphore is free; caller must release."""
    import nemo_coding_platform.mission_control_server as mcs
    mcs._llm_sem_acquire(timeout=2.0)
    mcs._LLM_SEM.release()
