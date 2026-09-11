"""ai_metrics with mocked Anthropic client — no live calls."""
from unittest.mock import MagicMock


def _set_response(mock_client, text):
    resp = MagicMock()
    resp.content = [MagicMock(text=text)]
    mock_client.messages.create.return_value = resp


def test_classify_receipt_true(mock_anthropic):
    import ai_metrics
    _set_response(mock_anthropic, '{"is_order": true}')
    assert ai_metrics.classify_receipt("Uber Eats <noreply@uber.com>", "Your Friday evening order") is True


def test_classify_receipt_parse_failure_defaults_false(mock_anthropic):
    import ai_metrics
    _set_response(mock_anthropic, "not json at all")
    assert ai_metrics.classify_receipt("x@uber.com", "??") is False


def test_classify_receipt_includes_snippet(mock_anthropic):
    _set_response(mock_anthropic, '{"is_order": true}')
    import ai_metrics
    assert ai_metrics.classify_receipt(
        "noreply@uber.com", "Your order", "Thanks for ordering, preview text"
    ) is True
    prompt = mock_anthropic.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "Thanks for ordering, preview text" in prompt


def test_call_json_fenced_json_label(mock_anthropic):
    import ai_metrics
    _set_response(mock_anthropic, '```json\n{"is_order": true}\n```')
    assert ai_metrics.classify_receipt("noreply@uber.com", "Your order") is True


def test_call_json_bare_fence(mock_anthropic):
    import ai_metrics
    _set_response(mock_anthropic, '```\n{"is_order": true}\n```')
    assert ai_metrics.classify_receipt("noreply@uber.com", "Your order") is True


def test_call_json_fenced_with_trailing_prose(mock_anthropic):
    """The exact production failure: fenced JSON followed by chatter."""
    import ai_metrics
    _set_response(
        mock_anthropic,
        '```json\n{"is_order": false}\n```\n\nThis is a ride receipt, not a food order.',
    )
    assert ai_metrics.classify_receipt("noreply@uber.com", "Your trip") is False
    mock_anthropic.messages.create.reset_mock()
    _set_response(
        mock_anthropic,
        '```json\n{"is_order": true}\n```\n\nThis is clearly an Uber Eats order.',
    )
    assert ai_metrics.classify_receipt("noreply@uber.com", "Your order") is True


def test_call_json_leading_prose_then_json(mock_anthropic):
    import ai_metrics
    _set_response(
        mock_anthropic,
        'Sure! Here is the classification:\n\n{"is_order": true}\n\nHope that helps.',
    )
    assert ai_metrics.classify_receipt("noreply@uber.com", "Your order") is True


def test_call_json_plain_unfenced_still_works(mock_anthropic):
    """Regression: the happy path must not change."""
    import ai_metrics
    _set_response(mock_anthropic, '{"is_social": true, "confidence": 0.42}')
    assert ai_metrics.classify_social_event("Dinner", "", "", []) == {
        "is_social": True, "confidence": 0.42,
    }


def test_call_json_braces_inside_string_value(mock_anthropic):
    """A `}` inside a string must not end the object early."""
    import ai_metrics
    _set_response(
        mock_anthropic,
        '```json\n{"reflection": "You wrote {curly} braces \\"and\\" quotes."}\n```\ndone',
    )
    assert ai_metrics.weekly_reflection(_card(), []) == 'You wrote {curly} braces "and" quotes.'


def test_call_json_nested_object(mock_anthropic):
    import ai_metrics
    _set_response(mock_anthropic, '```json\n{"reflection": "ok", "meta": {"a": {"b": 1}}}\n```')
    assert ai_metrics.weekly_reflection(_card(), []) == "ok"


def test_call_json_unparseable_still_returns_default_and_logs(mock_anthropic, caplog):
    """Contract on real failures is unchanged: default returned, error logged."""
    import logging
    import ai_metrics
    _set_response(mock_anthropic, "I'm sorry, I can't help with that. {not json")
    with caplog.at_level(logging.ERROR):
        assert ai_metrics.classify_receipt("x@uber.com", "??") is False
    assert "ai_metrics JSON call failed" in caplog.text


def test_classify_social_event(mock_anthropic):
    import ai_metrics
    _set_response(mock_anthropic, '{"is_social": true, "confidence": 0.92}')
    out = ai_metrics.classify_social_event("Dinner w/ Sam", "", "Bar Dough", ["Sam"])
    assert out == {"is_social": True, "confidence": 0.92}


def test_classify_social_event_failure_defaults(mock_anthropic):
    import ai_metrics
    _set_response(mock_anthropic, "garbage")
    out = ai_metrics.classify_social_event("Dentist", "", "", [])
    assert out == {"is_social": False, "confidence": 0.0}


def test_classify_social_event_non_numeric_confidence(mock_anthropic):
    import ai_metrics
    _set_response(mock_anthropic, '{"is_social": true, "confidence": "high"}')
    out = ai_metrics.classify_social_event("Dinner w/ friends", "", "Restaurant", ["Alice", "Bob"])
    assert out == {"is_social": True, "confidence": 0.0}


def test_classify_social_event_prompt_instructs_low_confidence_when_unsure(mock_anthropic):
    """The classifier may say 'can't tell' — inherently solo-or-social
    activities (movie, restaurant, hobby) should get a low confidence score
    rather than a confident guess when the signal doesn't decide it."""
    import ai_metrics
    _set_response(mock_anthropic, '{"is_social": true, "confidence": 0.9}')
    ai_metrics.classify_social_event("Movie night", "", "", [])
    prompt = mock_anthropic.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "do not guess" in prompt.lower()
    assert "low" in prompt.lower() and "confidence" in prompt.lower()


def test_classify_social_event_prompt_includes_examples(mock_anthropic):
    import ai_metrics
    _set_response(mock_anthropic, '{"is_social": true, "confidence": 0.9}')
    examples = [
        {"title": "Taco Tuesday", "user_is_social": True},
        {"title": "Team standup", "user_is_social": False},
    ]
    ai_metrics.classify_social_event("Dinner", "", "", [], examples=examples)
    prompt = mock_anthropic.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "The user has corrected past classifications:" in prompt
    assert '"Taco Tuesday" IS social' in prompt
    assert '"Team standup" IS NOT social' in prompt


def test_classify_social_event_prompt_omits_examples_block_when_empty(mock_anthropic):
    import ai_metrics
    _set_response(mock_anthropic, '{"is_social": true, "confidence": 0.9}')
    ai_metrics.classify_social_event("Dinner", "", "", [], examples=None)
    prompt = mock_anthropic.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "corrected past classifications" not in prompt

    ai_metrics.classify_social_event("Dinner", "", "", [], examples=[])
    prompt = mock_anthropic.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "corrected past classifications" not in prompt


def _card():
    return {
        "week_start": "2026-07-13", "week_end": "2026-07-19",
        "metrics": {
            "gym": {"label": "Gym sessions", "count": 3, "target": 3, "direction": "floor", "hit": True},
            "delivery": {"label": "Delivery orders", "count": 2, "target": 1, "direction": "ceiling", "hit": False},
        },
    }


def test_weekly_reflection_returns_text(mock_anthropic):
    import ai_metrics
    _set_response(mock_anthropic, '{"reflection": "You held the line on gym."}')
    assert ai_metrics.weekly_reflection(_card(), ["a pattern"]) == "You held the line on gym."
    prompt = mock_anthropic.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "Gym sessions: 3" in prompt and "a pattern" in prompt


def test_weekly_reflection_empty_on_garbage(mock_anthropic):
    import ai_metrics
    _set_response(mock_anthropic, "not json")
    assert ai_metrics.weekly_reflection(_card(), []) == ""


def test_weekly_reflection_empty_on_non_dict_json(mock_anthropic):
    import ai_metrics
    _set_response(mock_anthropic, "[1, 2, 3]")
    assert ai_metrics.weekly_reflection(_card(), []) == ""


def _bank_row(simplefin_id="tx1", amount=-42.00, payee="Amazon", description="AMZN MKTP", **extra):
    row = {
        "simplefin_id": simplefin_id, "amount": amount, "payee": payee,
        "description": description,
    }
    row.update(extra)
    return row


def test_suggest_bank_flows_valid_mapping_passes(mock_anthropic):
    import ai_metrics
    _set_response(mock_anthropic, '{"tx1": "spending"}')
    rows = [_bank_row("tx1", amount=-42.00)]
    out = ai_metrics.suggest_bank_flows(rows, [])
    assert out == {"tx1": "spending"}


def test_suggest_bank_flows_inflow_row_suggested_spending_dropped(mock_anthropic):
    import ai_metrics
    _set_response(mock_anthropic, '{"tx1": "spending"}')
    rows = [_bank_row("tx1", amount=100.00)]  # inflow
    out = ai_metrics.suggest_bank_flows(rows, [])
    assert out == {}


def test_suggest_bank_flows_unknown_id_dropped(mock_anthropic):
    import ai_metrics
    _set_response(mock_anthropic, '{"tx1": "spending", "tx-unknown": "income"}')
    rows = [_bank_row("tx1", amount=-10.00)]
    out = ai_metrics.suggest_bank_flows(rows, [])
    assert out == {"tx1": "spending"}


def test_suggest_bank_flows_null_value_dropped(mock_anthropic):
    import ai_metrics
    _set_response(mock_anthropic, '{"tx1": null}')
    rows = [_bank_row("tx1", amount=-10.00)]
    out = ai_metrics.suggest_bank_flows(rows, [])
    assert out == {}


def test_suggest_bank_flows_non_dict_model_return_is_empty(mock_anthropic):
    import ai_metrics
    _set_response(mock_anthropic, "[1, 2, 3]")
    rows = [_bank_row("tx1", amount=-10.00)]
    out = ai_metrics.suggest_bank_flows(rows, [])
    assert out == {}


def test_suggest_bank_flows_empty_rows_no_call(mock_anthropic):
    import ai_metrics
    out = ai_metrics.suggest_bank_flows([], [])
    assert out == {}
    mock_anthropic.messages.create.assert_not_called()


def test_suggest_bank_flows_prompt_excludes_note_and_extra_fields(mock_anthropic):
    import ai_metrics
    _set_response(mock_anthropic, '{"tx1": "spending"}')
    rows = [_bank_row(
        "tx1", amount=-10.00,
        user_note="SENTINEL-NOTE-TEXT",
        account_name="SENTINEL-ACCOUNT-NAME",
        memo="SENTINEL-JUNK-KEY",
    )]
    examples = [{
        "payee": "Costco", "description": "COSTCO WHSE", "side": "outflow",
        "user_flow": "spending", "user_note": "SENTINEL-NOTE-TEXT",
    }]
    ai_metrics.suggest_bank_flows(rows, examples)
    prompt = mock_anthropic.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "SENTINEL-NOTE-TEXT" not in prompt
    assert "SENTINEL-ACCOUNT-NAME" not in prompt
    assert "SENTINEL-JUNK-KEY" not in prompt
    assert "-10" not in prompt
    assert "10.0" not in prompt


def test_classify_work_ride_returns_parsed_dict(mock_anthropic):
    import ai_metrics
    _set_response(mock_anthropic, '{"is_work": true, "confidence": 0.8}')
    out = ai_metrics.classify_work_ride("Uber", "Your Tuesday trip with Uber", "Jul 14, 2026 7:15 AM")
    assert out == {"is_work": True, "confidence": 0.8}


def test_classify_work_ride_prompt_includes_subject_and_snippet(mock_anthropic):
    import ai_metrics
    _set_response(mock_anthropic, '{"is_work": false, "confidence": 0.5}')
    ai_metrics.classify_work_ride("Lyft", "Your ride with Lyft", "Airport pickup at 6am")
    prompt = mock_anthropic.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "Your ride with Lyft" in prompt
    assert "Airport pickup at 6am" in prompt


def test_classify_work_ride_garbage_json_defaults(mock_anthropic):
    import ai_metrics
    _set_response(mock_anthropic, "not json at all")
    out = ai_metrics.classify_work_ride("Uber", "Your trip", "")
    assert out == {"is_work": False, "confidence": 0.0}


def test_classify_work_ride_prompt_includes_examples_when_present(mock_anthropic):
    import ai_metrics
    _set_response(mock_anthropic, '{"is_work": true, "confidence": 0.9}')
    examples = [
        {"subject": "Your trip to the airport", "user_is_work": True},
        {"subject": "Your ride home", "user_is_work": False},
    ]
    ai_metrics.classify_work_ride("Uber", "Your trip", "", examples=examples)
    prompt = mock_anthropic.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "The user has corrected past classifications:" in prompt
    assert '"Your trip to the airport" IS work' in prompt
    assert '"Your ride home" IS NOT work' in prompt


def test_classify_work_ride_prompt_omits_examples_block_when_absent(mock_anthropic):
    import ai_metrics
    _set_response(mock_anthropic, '{"is_work": false, "confidence": 0.5}')
    ai_metrics.classify_work_ride("Uber", "Your trip", "", examples=None)
    prompt = mock_anthropic.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "corrected past classifications" not in prompt

    ai_metrics.classify_work_ride("Uber", "Your trip", "", examples=[])
    prompt = mock_anthropic.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "corrected past classifications" not in prompt


def _capture_reports(monkeypatch):
    import ai_metrics
    calls = []
    monkeypatch.setattr(ai_metrics, "report",
                        lambda *args, **kwargs: calls.append((args, kwargs)))
    return calls


def test_every_call_reports_usage_to_coach_web(mock_anthropic, monkeypatch):
    """Spend is only knowable if every Anthropic call reports its token counts."""
    import ai_metrics
    calls = _capture_reports(monkeypatch)
    resp = MagicMock()
    resp.content = [MagicMock(text='{"is_order": true}')]
    resp.usage = MagicMock(input_tokens=11, output_tokens=7)
    mock_anthropic.messages.create.return_value = resp

    ai_metrics.classify_receipt("noreply@uber.com", "Your order")

    assert len(calls) == 1
    args, _ = calls[0]
    assert args[0] == "life-tracker"     # must match a name in coach-web apps.yaml
    assert args[1] == ai_metrics.MODEL
    assert args[2] is resp.usage


def test_unparseable_response_still_reports_usage(mock_anthropic, monkeypatch):
    """A call that returns garbage was still billed — report before parsing."""
    import ai_metrics
    calls = _capture_reports(monkeypatch)
    _set_response(mock_anthropic, "not json at all")

    assert ai_metrics.classify_receipt("x@uber.com", "??") is False
    assert len(calls) == 1


def test_reporting_failure_never_propagates(mock_anthropic, monkeypatch):
    """The reporter's contract is never-raise. If one ever did, _call_json's
    boundary contains it — the caller gets the safe default, never an
    exception. Pinned because the failure mode would otherwise be a crash in
    every classification path at once."""
    import ai_metrics

    def boom(*args, **kwargs):
        raise RuntimeError("coach-web unreachable")

    monkeypatch.setattr(ai_metrics, "report", boom)
    _set_response(mock_anthropic, '{"is_order": true}')
    assert ai_metrics.classify_receipt("noreply@uber.com", "Your order") is False
