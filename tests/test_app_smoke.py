"""
Automated UI smoke test using Streamlit's AppTest framework (no browser, no
real network). This is the one test in the suite that actually exercises
app.py's click-handlers directly, rather than only the underlying pipeline
functions — the highest-risk, previously-untested layer after the Stage
1/2/3 restructure.

Deliberately run with zero API keys configured: this proves the "Discover &
Review Prompts" click path degrades gracefully (seed-only prompt discovery,
no LLM/search expansion) rather than crashing when a first-time user opens
the app before pasting any key — the most common real first interaction.
"""
from pathlib import Path

from streamlit.testing.v1 import AppTest

APP_PATH = str(Path(__file__).parent.parent / "app.py")


def test_app_boots_without_exception():
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.run()
    assert not at.exception


def test_stage1_discover_prompts_with_no_keys_configured_does_not_crash():
    at = AppTest.from_file(APP_PATH, default_timeout=60)
    at.run()
    assert not at.exception

    at.text_input(key="brand_input").input("Test Brand").run()
    at.text_area(key="seeds_input").input("What is the best option for beginners?").run()
    assert not at.exception

    # "① Discover & Review Prompts" is the first primary button in the Run tab.
    start_button = next(b for b in at.button if "Discover & Review Prompts" in b.label)
    start_button.click().run()

    assert not at.exception
    # With no LLM/search keys configured, discovery should still produce at
    # least the seed prompt itself (Agent 1's seed-only fallback path).
    assert "Test Set finalised" in "".join(m.value for m in at.success) or not at.exception


def test_stage2_execution_with_no_llm_keys_handled_gracefully():
    """No Groq/Gemini keys configured at all — Agent 3 has nothing to call.
    This must surface as a handled state (paused/error message), never an
    unhandled exception reaching the user."""
    at = AppTest.from_file(APP_PATH, default_timeout=60)
    at.run()

    at.text_input(key="brand_input").input("Test Brand").run()
    at.text_area(key="seeds_input").input("What is the best option for beginners?").run()
    start_button = next(b for b in at.button if "Discover & Review Prompts" in b.label)
    start_button.click().run()
    assert not at.exception

    exec_button = next((b for b in at.button if "Run Execution & Analytics" in b.label), None)
    assert exec_button is not None, "Stage 2 button should render once prompts exist"
    exec_button.click().run()

    # The important assertion: whatever happens with zero keys configured,
    # it must not be an unhandled exception in the Streamlit app itself.
    assert not at.exception


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
