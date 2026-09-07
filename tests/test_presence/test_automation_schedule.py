import pytest

from ngram.presence.automations.schedule import ScheduleParseError, parse_schedule


def test_parse_schedule_examples() -> None:
    assert parse_schedule("every morning at 8am") == "0 8 * * *"
    assert parse_schedule("every day at 9am") == "0 9 * * *"
    assert parse_schedule("every 3 hours") == "0 */3 * * *"
    assert parse_schedule("every sunday evening") == "0 18 * * 0"
    assert parse_schedule("twice a day") == "0 8,20 * * *"
    assert parse_schedule("every weekday at 7am") == "0 7 * * 1-5"
    assert parse_schedule("once a week on friday") == "0 12 * * 5"
    assert parse_schedule("every 30 minutes") == "*/30 * * * *"
    assert parse_schedule("every night at 11pm") == "0 23 * * *"
    assert parse_schedule("every monday and thursday") == "0 9 * * 1,4"
    assert parse_schedule("every other day") == "0 9 */2 * *"
    assert parse_schedule("first of every month") == "0 9 1 * *"


def test_parse_schedule_empty_raises() -> None:
    with pytest.raises(ScheduleParseError):
        parse_schedule("")


def test_parse_schedule_gibberish() -> None:
    with pytest.raises(ScheduleParseError):
        parse_schedule("whenever the moon is full and also pizza")
