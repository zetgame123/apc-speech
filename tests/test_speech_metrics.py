from app.metrics.speech_metrics import SpeechMetricsCalculator


def test_deterministic_speech_metrics_calculation():
    calc = SpeechMetricsCalculator()

    segments = [
        {
            "start": 0.0,
            "end": 10.0,
            "speaker_role": "manager",
            "text": "Здравствуйте агентство наружной рекламы слушаю вас",
        },
        {
            "start": 10.5,
            "end": 20.0,
            "speaker_role": "client",
            "text": "Добрый день подскажите стоимость щитов три на шесть",
        },
        # Overlapping interruption: manager starts at 19.5, client ends at 20.0 (0.5s overlap)
        {
            "start": 19.5,
            "end": 30.0,
            "speaker_role": "manager",
            "text": "Да конечно у нас есть отличные варианты в Балашихе",
        },
    ]

    total_duration = 35.0
    metrics = calc.compute(segments, total_duration)

    assert metrics["manager_speech_share"] > 0.4
    assert metrics["client_speech_share"] > 0.2
    assert metrics["interruptions_count"] == 1
    assert metrics["silence_duration_sec"] > 0
    assert metrics["manager_wpm"] > 0
    assert metrics["client_wpm"] > 0


def test_speech_metrics_overlap_silence_union():
    """A17 check: Manager 0-10s, Client 5-15s, Call 20s. Real silence must be exactly 5.0s, not 0s."""
    calc = SpeechMetricsCalculator()
    segments = [
        {"start": 0.0, "end": 10.0, "speaker_role": "manager", "text": "Здравствуйте слушаю вас"},
        {"start": 5.0, "end": 15.0, "speaker_role": "client", "text": "Да добрый день"},
    ]
    metrics = calc.compute(segments, call_duration_sec=20.0)
    assert metrics["silence_duration_sec"] == 5.0
    # Overlap from 5.0 to 10.0 is 5.0s > 0.3s -> interruption = 1
    assert metrics["interruptions_count"] == 1
