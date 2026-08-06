from fleet.pricing import PRICES, cost, usd


def test_fresh_input_and_output():
    assert cost("claude-opus-5-5", {"input_tokens": 1_000_000, "output_tokens": 1_000_000}) == 24.0


def test_cache_reads_and_writes_priced_separately():
    c = cost("claude-opus-5-5", {"input_tokens": 1_000_000, "cache_read_input_tokens": 800_000,
                                 "cache_creation_input_tokens": 100_000})
    # 100k fresh at $4, 100k written at 1.25x, 800k read at $0.20
    assert abs(c - (0.4 + 0.5 + 0.16)) < 1e-9


def test_unknown_model_priced_as_default():
    u = {"input_tokens": 1000, "output_tokens": 100}
    assert cost("some-future-model", u) == cost(None, u) == cost("claude-opus-5-5", u)
    assert cost("claude-haiku-4-5", u) < cost("claude-opus-5-5", u)


def test_price_table_sane():
    assert all(o > i > r > 0 for i, o, r in PRICES.values())
    assert usd(0.01234) == "$0.0123" and usd(12.5) == "$12.50"


def test_bedrock_and_vertex_ids_map_to_first_party_rows():
    from fleet.pricing import canonical
    for mid in ("anthropic.claude-opus-5-5", "us.anthropic.claude-opus-5-5", "global.anthropic.claude-opus-5-5",
                "anthropic.claude-opus-5-5-v1:0", "claude-opus-5-5@20260901", "claude-opus-5-5"):
        assert canonical(mid) == "claude-opus-5-5", mid
    assert canonical("anthropic.claude-haiku-4-5-20251001-v1:0") == "claude-haiku-4-5"
    assert canonical("claude-sonnet-4-6") == "claude-sonnet-4-6"  # the trailing -6 is not a suffix
    u = {"input_tokens": 1000, "output_tokens": 100}
    assert cost("us.anthropic.claude-haiku-4-5-20251001-v1:0", u) == cost("claude-haiku-4-5", u)


def test_price_file_overrides_list_prices(tmp_path, monkeypatch):
    import json
    import pytest
    from fleet.pricing import load_prices
    f = tmp_path / "prices.json"
    f.write_text(json.dumps({"us.anthropic.claude-opus-5-5": {"input": 4.4, "output": 22},
                             "claude-haiku-4-5": {"input": 1, "output": 5, "cache_read": 0.2, "cache_write": 2}}))
    monkeypatch.setenv("FLEET_PRICES", str(f))
    m = {"input_tokens": 1_000_000, "output_tokens": 1_000_000}
    assert cost("us.anthropic.claude-opus-5-5", m) == 4.4 + 22   # exact ID
    assert cost("claude-opus-5-5", m) == 24.0                     # other IDs keep list prices
    c = {"input_tokens": 3_000_000, "cache_read_input_tokens": 1_000_000, "cache_creation_input_tokens": 1_000_000}
    assert abs(cost("claude-haiku-4-5@20251001", c) - (1 + 0.2 + 2)) < 1e-9  # via the first-party ID
    f.write_text(json.dumps({"claude-opus-5-5": {"input": 4.4, "output": 22}}))
    assert cost(None, m) == 4.4 + 22  # no model ID (scripted backend) = the default model's rates
    f.write_text('{"x": {"output": 1}}')
    with pytest.raises(ValueError, match="bad price file"):
        load_prices(f)
