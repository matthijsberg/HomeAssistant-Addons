"""
Unit Tests for Open HEMS i18n Localization Engine
=================================================
"""

import pytest
from api.i18n import t, get_supported_languages, load_locale


def test_i18n_supported_languages():
    langs = get_supported_languages()
    assert "nl" in langs
    assert "en" in langs


def test_i18n_translation_and_fallback():
    # Known keys in both NL and EN
    nl_title = t("app.title", lang="nl")
    en_title = t("app.title", lang="en")
    assert nl_title == "Open HEMS"
    assert en_title == "Open HEMS"

    nl_box = t("dhw.box_title", lang="nl")
    en_box = t("dhw.box_title", lang="en")
    assert "Buffer Efficiëntie" in nl_box
    assert "Buffer Efficiency" in en_box

    # Missing key fallback
    missing = t("some.totally.fake.key.123", lang="en")
    assert missing == "some.totally.fake.key.123"


def test_i18n_interpolation():
    msg_nl = t("dhw.badge_night_planned", lang="nl", start="02:00", end="04:00", temp=50.0)
    assert "02:00–04:00" in msg_nl
    assert "50.0°C" in msg_nl

    msg_en = t("dhw.badge_night_planned", lang="en", start="02:00", end="04:00", temp=50.0)
    assert "Night Charge Scheduled" in msg_en
    assert "02:00–04:00" in msg_en
    assert "50.0°C" in msg_en
