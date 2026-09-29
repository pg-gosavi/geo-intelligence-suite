import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.key_manager import KeyManager, KeyPool, mask_key
from tests.fakes import FlakyThenRecoverProvider
from src.providers.base import QuotaExceededError


def test_mask_key():
    masked = mask_key("sk-abcdefgh1234")
    assert masked.endswith("1234")
    assert masked != "sk-abcdefgh1234"
    assert len(masked) == len("sk-abcdefgh1234")
    assert mask_key("") == ""
    assert mask_key("abc") == "abc"  # shorter than 4 chars: shown as-is


def test_pool_rotates_through_keys_in_order():
    pool = KeyPool.from_csv("gemini", "key-a,key-b,key-c")
    assert pool.current().key == "key-a"
    pool.rotate("429")
    assert pool.current().key == "key-b"
    pool.rotate("429")
    assert pool.current().key == "key-c"


def test_pool_reports_exhausted_after_all_keys_fail():
    pool = KeyPool.from_csv("gemini", "key-a,key-b")
    pool.rotate("429")
    assert not pool.exhausted()
    pool.rotate("429")
    assert pool.exhausted()
    assert pool.current() is None


def test_key_manager_rotate_and_exhaustion():
    km = KeyManager()
    km.set_keys("gemini", "k1,k2")
    assert km.current_key("gemini") == "k1"
    nxt = km.rotate("gemini", error="429")
    assert nxt == "k2"
    assert not km.is_exhausted("gemini")
    nxt2 = km.rotate("gemini", error="429")
    assert nxt2 is None
    assert km.is_exhausted("gemini")


def test_key_manager_loads_from_env_dict():
    km = KeyManager()
    km.load_from_env({"GEMINI_API_KEYS": "a,b,c", "GROQ_API_KEYS": "x"})
    assert [k.key for k in km.pool("gemini").keys] == ["a", "b", "c"]
    assert [k.key for k in km.pool("groq").keys] == ["x"]
    assert km.pool("claude").has_keys() is False


def test_provider_level_rotation_retries_same_unit_of_work():
    """Simulates agent3's rotate-on-429 loop directly against a fake provider
    whose 'expired-key' always 429s and any other key succeeds."""
    provider = FlakyThenRecoverProvider("Gemini", canned={"hello": ("hi there", [])}, bad_key="expired-key")
    km = KeyManager()
    km.set_keys("gemini", "expired-key,fresh-key")

    api_key = km.current_key("gemini")
    result = None
    for _ in range(len(km.pool("gemini"))):
        try:
            result = provider.generate("hello", api_key)
            break
        except QuotaExceededError:
            api_key = km.rotate("gemini", error="429")
            if api_key is None:
                break
    assert result is not None
    assert result.text == "hi there"
    assert provider.calls_by_key == {"expired-key": 1, "fresh-key": 1}


def test_key_manager_any_configured():
    km = KeyManager()
    assert km.any_configured("gemini") is False
    km.set_keys("gemini", "k1")
    assert km.any_configured("gemini") is True


def test_status_table_masks_keys():
    pool = KeyPool.from_csv("groq", "gsk-1234567890abcd")
    table = pool.status_table()
    assert table[0]["key"].endswith("abcd")
    assert "1234567890abcd" not in table[0]["key"]
    assert table[0]["valid"] is None
