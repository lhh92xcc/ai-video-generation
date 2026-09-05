from app.providers.macos_say_tts import MacOSSayTTSProvider


def test_macos_say_rate_conversion_is_bounded() -> None:
    assert MacOSSayTTSProvider._rate_to_words_per_minute("+0%") == 180
    assert MacOSSayTTSProvider._rate_to_words_per_minute("+20%") == 216
    assert MacOSSayTTSProvider._rate_to_words_per_minute("-50%") == 90
    assert MacOSSayTTSProvider._rate_to_words_per_minute("not-a-rate") == 180
    assert MacOSSayTTSProvider._rate_to_words_per_minute("+999%") == 360
