from recoup.models.enums import ActionType, Band, FailureClass, TerminalState, Tier


def test_failure_classes_are_exactly_the_six_spec_classes():
    assert {c.value for c in FailureClass} == {
        "INSUFFICIENT_FUNDS",
        "INSTRUMENT_INVALID",
        "MANDATE_REVOKED",
        "TRANSIENT_ISSUER",
        "RISK_DECLINE",
        "UNCLASSIFIED",
    }


def test_tiers_are_ordered_and_comparable():
    assert Tier.T1_NOTIFY < Tier.T2_REQUEST_ACTION < Tier.T3_FINAL_NOTICE < Tier.T4_TERMINAL
    assert int(Tier.T1_NOTIFY) == 1
    assert int(Tier.T4_TERMINAL) == 4


def test_terminal_states_are_the_four_spec_states():
    assert {s.value for s in TerminalState} == {
        "recovered",
        "unrecovered",
        "voluntary_churn",
        "manual_review",
    }


def test_action_types_cover_every_thing_the_pipeline_can_do():
    assert {a.value for a in ActionType} == {
        "retry_charge",
        "request_instrument_update",
        "send_message",
        "stop",
        "escalate_manual_review",
    }


def test_bands_are_low_mid_high():
    assert [b.value for b in Band] == ["low", "mid", "high"]
