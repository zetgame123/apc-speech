from scripts.eval_provider_cost import provider_cost


def test_provider_cost_known_values():
    r = provider_cost(1_000_000, 500_000, 1.0, 4.0, 1000)
    assert r["per_call_usd"] == 3.0
    assert r["per_1000_calls_usd"] == 3000.0
    assert r["total_usd"] == 3000.0


def test_provider_cost_zero_prices():
    r = provider_cost(802, 235, 0.0, 0.0, 1000)
    assert r["per_call_usd"] == 0.0
    assert r["total_usd"] == 0.0
