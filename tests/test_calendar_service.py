"""get_events_range: mapping, and exclusion of declined/cancelled/all-day/birthday events."""
from unittest.mock import MagicMock


def _fake_service(items):
    service = MagicMock()
    service.events.return_value.list.return_value.execute.return_value = {"items": items, "nextPageToken": None}
    return service


def _item(**overrides):
    base = {
        "id": "ev1",
        "status": "confirmed",
        "summary": "Dinner w/ Sam",
        "start": {"dateTime": "2026-07-15T19:00:00-06:00"},
        "end": {"dateTime": "2026-07-15T21:00:00-06:00"},
        "description": "",
        "location": "Bar Dough",
        "attendees": [
            {"self": True, "responseStatus": "accepted"},
            {"displayName": "Sam", "responseStatus": "accepted"},
        ],
    }
    base.update(overrides)
    return base


def test_maps_events(monkeypatch):
    from services import calendar_service
    monkeypatch.setattr(calendar_service, "_get_service", lambda: _fake_service([_item()]))
    events = calendar_service.get_events_range(days_back=3)
    assert len(events) == 1
    ev = events[0]
    assert ev["event_id"] == "ev1"
    assert ev["title"] == "Dinner w/ Sam"
    assert ev["start_datetime"] == "2026-07-15T19:00:00-06:00"
    assert ev["attendees"] == ["Sam"]


def test_maps_recurring_event_id(monkeypatch):
    from services import calendar_service
    monkeypatch.setattr(
        calendar_service, "_get_service",
        lambda: _fake_service([_item(recurringEventId="series-abc"), _item(id="oneoff")]),
    )
    events = calendar_service.get_events_range(days_back=3)
    by_id = {e["event_id"]: e for e in events}
    assert by_id["ev1"]["recurring_event_id"] == "series-abc"
    assert by_id["oneoff"]["recurring_event_id"] is None  # a one-off event has none


def test_excludes_declined_cancelled_allday_birthday(monkeypatch):
    from services import calendar_service
    items = [
        _item(id="declined", attendees=[{"self": True, "responseStatus": "declined"}]),
        _item(id="cancelled", status="cancelled"),
        _item(id="allday", start={"date": "2026-07-15"}, end={"date": "2026-07-16"}),
        _item(id="bday", summary="Mom's birthday"),
        _item(id="keeper"),
    ]
    monkeypatch.setattr(calendar_service, "_get_service", lambda: _fake_service(items))
    events = calendar_service.get_events_range(days_back=3)
    assert [e["event_id"] for e in events] == ["keeper"]
