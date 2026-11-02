from datetime import date

import pytest

from paperscout.commands import SPECS, UsageError, parse_raw, spec_for, tokenize_args


def test_multiword_topic_without_quotes():
    p = parse_raw(SPECS["recent"], "text-guided 3D Gaussian scene editing")
    assert p.text == "text-guided 3D Gaussian scene editing"
    assert p.get("limit") == 10 and p.get("days") is None


def test_options_anywhere_and_quoted_folder():
    p = parse_raw(SPECS["recent"], 'diffusion models --folder "Diffusion Models" --limit 5 --days 30')
    assert p.text == "diffusion models"
    assert p.get("folder") == "Diffusion Models"
    assert p.get("limit") == 5 and p.get("days") == 30


def test_rank_quoted_folder_and_flags():
    p = parse_raw(SPECS["rank"], '"3D Scene Editing" --unread')
    assert p.text == "3D Scene Editing" and p.get("unread") is True and p.get("all") is False


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Kirchhoff's law of circuits", ["Kirchhoff's", "law", "of", "circuits"]),
        ("“smart quoted name” --x", ["smart quoted name", "--x"]),
        ("--folder=\"My Folder\" rest", ["--folder=My Folder", "rest"]),
        ("'single quoted' word", ["single quoted", "word"]),
        ('unbalanced "quote here', ["unbalanced", '"quote', "here"]),
    ],
)
def test_tokenizer(raw, expected):
    assert tokenize_args(raw) == expected


def test_powershell_pipe_bom_is_ignored():
    p = parse_raw(SPECS["rank"], '﻿"3D Scene Editing" --unread\r\n')
    assert p.text == "3D Scene Editing" and p.get("unread") is True


def test_em_dash_options_are_normalized():
    p = parse_raw(SPECS["recent"], "segmentation —days 90")
    assert p.get("days") == 90 and p.text == "segmentation"


def test_dates_and_validation():
    p = parse_raw(SPECS["recent"], "x --since 2026-01-01 --until 2026-03-01")
    assert p.get("since") == date(2026, 1, 1) and p.get("until") == date(2026, 3, 1)
    with pytest.raises(UsageError, match="date"):
        parse_raw(SPECS["recent"], "x --since 01/02/2026")
    with pytest.raises(UsageError, match="at most 50"):
        parse_raw(SPECS["recent"], "x --limit 500")
    with pytest.raises(UsageError, match="whole number"):
        parse_raw(SPECS["recent"], "x --days ninety")
    with pytest.raises(UsageError, match="more than once"):
        parse_raw(SPECS["recent"], "x --days 3 --days 4")
    with pytest.raises(UsageError, match="needs a value"):
        parse_raw(SPECS["recent"], "x --folder")


def test_unknown_option_is_rejected_with_suggestion():
    with pytest.raises(UsageError, match="Did you mean --days"):
        parse_raw(SPECS["recent"], "topic --dayz 30")


def test_double_dash_makes_rest_literal():
    p = parse_raw(SPECS["discover"], "-- --weird topic")
    assert p.text == "--weird topic"


def test_feedback_positionals():
    p = parse_raw(SPECS["feedback"], '0000000000000000000000000000000000000005 irrelevant "Focuses on animation, not editing."')
    assert p.positionals == ["0000000000000000000000000000000000000005", "irrelevant", "Focuses on animation, not editing."]


def test_choice_validation():
    with pytest.raises(UsageError, match="must be one of"):
        parse_raw(SPECS["rank"], "--all --source dropbox")
    with pytest.raises(UsageError, match="must be one of"):
        parse_raw(SPECS["note"], "x --pass third")


def test_slash_names_map_to_commands():
    assert spec_for("reading-plan").name == "plan"
    assert spec_for("/paper-feedback").name == "feedback"
    assert spec_for("research-help").name == "help"
    with pytest.raises(UsageError, match="Unknown command"):
        spec_for("rnak")
