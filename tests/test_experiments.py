from ecom_agents.experiments import assign_variant, assignment_score, describe, variant_config


def test_assignment_is_stable():
    assert assign_variant("U1001", "campaign_v1") == assign_variant("U1001", "campaign_v1")
    assert assignment_score("U1001", "campaign_v1") == assignment_score("U1001", "campaign_v1")


def test_disabled_experiment_puts_everyone_in_a():
    for user in ("U1001", "U1002", "new-user"):
        assert assign_variant(user, "x", enabled=False) == "A"


def test_split_is_roughly_balanced():
    variants = [assign_variant(f"user-{i}", "campaign_v1", split=0.5) for i in range(1000)]
    share_a = variants.count("A") / len(variants)
    assert 0.42 < share_a < 0.58


def test_variants_differ_on_pricing():
    a = variant_config("A")
    b = variant_config("B")
    assert a["enable_pricing"] is False
    assert b["enable_pricing"] is True
    assert b["discount_cap"] > a["discount_cap"]


def test_describe_includes_score_and_config():
    result = describe("U1001", "campaign_v1")
    assert result["variant"] in {"A", "B"}
    assert 0.0 <= result["score"] <= 1.0
    assert "description" in result["config"]
