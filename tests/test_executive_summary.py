from src.agents import executive_summary as es
from src.providers.base import ProviderError, ProviderResponse, QuotaExceededError


def _fake_gevs():
    return {
        "brand": "Acme Mutual Fund",
        "main_table": [
            {"entity": "Leader Fund", "gevs_top3": 60.0, "gevs_overall": 60.0, "rank": 1, "total_mentions": 18},
            {"entity": "Acme Mutual Fund", "gevs_top3": 40.0, "gevs_overall": 45.0, "rank": 2, "total_mentions": 12},
        ],
    }


def _fake_citation_intel():
    return [{"domain": "groww.in", "total_citations": 7, "source_quality": "editorial"}]


def _fake_gap_result():
    return {"gap_rows": [{}, {}], "analyses": []}


def _fake_targets():
    return {"content_gaps_identified": 2, "quick_wins": 1, "medium_term": 1, "strategic": 0}


def _fake_draft_result():
    return {"strategy_rows": [{"num": 1}, {"num": 2}], "drafts": {}}


def _fake_citation_accuracy(pct=83.3, meets=True):
    return {"claimed_total": 6, "verified_total": 5, "accuracy_pct": pct, "target_pct": 80.0, "meets_target": meets}


def _fake_prompt_relevance(pct=66.7):
    return {"total_prompts": 3, "rated_count": 3, "relevant_count": 2, "relevance_pct": pct, "fully_rated": True}


def _fake_processing_speed(elapsed=95.0, meets=True):
    return {"stages": {}, "total_elapsed_s": elapsed, "target_s": 180.0, "meets_target": meets}


def _build_data(**overrides):
    config = {"brand": "Acme Mutual Fund", "sector": "mutual_funds", "region": "India"}
    data = es.build_summary_data(
        config, _fake_gevs(), _fake_citation_intel(), _fake_gap_result(), _fake_targets(),
        _fake_draft_result(), _fake_citation_accuracy(), _fake_prompt_relevance(), _fake_processing_speed(),
    )
    data.update(overrides)
    return data


def test_build_summary_data_pulls_correct_brand_stats():
    data = _build_data()
    assert data["brand_gevs_top3"] == 40.0
    assert data["brand_rank"] == 2
    assert data["leader_brand"] == "Leader Fund"
    assert data["gap_to_leader_pp"] == 20.0
    assert data["is_leader"] is False


def test_build_summary_data_leader_case_has_zero_gap():
    config = {"brand": "Leader Fund", "sector": "mutual_funds", "region": "India"}
    gevs = _fake_gevs()
    gevs["brand"] = "Leader Fund"  # gevs_result["brand"] always matches config["brand"] in real usage
    data = es.build_summary_data(
        config, gevs, _fake_citation_intel(), _fake_gap_result(), _fake_targets(),
        _fake_draft_result(), _fake_citation_accuracy(), _fake_prompt_relevance(), _fake_processing_speed(),
    )
    assert data["is_leader"] is True
    assert data["gap_to_leader_pp"] == 0.0


def test_template_summary_includes_every_number_and_no_llm_needed():
    data = _build_data()
    summary = es.render_template_summary(data)
    assert "40.0%" in summary
    assert "Leader Fund" in summary
    assert "83.3%" in summary  # citation accuracy
    assert "66.7%" in summary  # prompt relevance
    assert "Acme Mutual Fund" in summary


def test_template_summary_handles_unrated_and_unmeasured_kpis():
    data = _build_data()
    data["citation_accuracy_pct"] = None
    data["prompt_relevance_pct"] = None
    summary = es.render_template_summary(data)
    assert "not enough claimed citations" in summary
    assert "not rated yet" in summary


def test_llm_polish_falls_back_to_template_with_no_client():
    data = _build_data()
    result = es.render_llm_polished_summary(data, llm_client=None, llm_api_key=None)
    assert result == es.render_template_summary(data)


class _FakeLLM:
    def __init__(self, text=None, raise_exc=None):
        self.text = text
        self.raise_exc = raise_exc
        self.called_with = None

    def generate(self, prompt, api_key):
        self.called_with = (prompt, api_key)
        if self.raise_exc:
            raise self.raise_exc
        return ProviderResponse(text=self.text, raw={}, cited_urls=[])


def test_llm_polish_uses_model_output_when_available():
    data = _build_data()
    llm = _FakeLLM(text="Acme trails the category leader by 20 points but citation accuracy is strong.")
    result = es.render_llm_polished_summary(data, llm_client=llm, llm_api_key="fake-key")
    assert "Acme trails the category leader" in result
    assert llm.called_with is not None


def test_llm_polish_falls_back_to_template_on_quota_error():
    data = _build_data()
    llm = _FakeLLM(raise_exc=QuotaExceededError("rate limited"))
    result = es.render_llm_polished_summary(data, llm_client=llm, llm_api_key="fake-key")
    assert result == es.render_template_summary(data)


def test_llm_polish_falls_back_to_template_on_provider_error():
    data = _build_data()
    llm = _FakeLLM(raise_exc=ProviderError("bad request"))
    result = es.render_llm_polished_summary(data, llm_client=llm, llm_api_key="fake-key")
    assert result == es.render_template_summary(data)


def test_llm_polish_empty_response_falls_back_to_template():
    data = _build_data()
    llm = _FakeLLM(text="   ")
    result = es.render_llm_polished_summary(data, llm_client=llm, llm_api_key="fake-key")
    assert result == es.render_template_summary(data)


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
