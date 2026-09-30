from app.diarization.roles import RoleClassifier


def test_stereo_role_assignment():
    classifier = RoleClassifier()

    ch0_segs = [
        {"start": 0.0, "end": 5.0, "text": "Компания Аутдор слушаю вас"},
    ]
    ch1_segs = [
        {"start": 5.5, "end": 10.0, "text": "Здравствуйте, нужен щит"},
    ]

    combined, conf = classifier.assign_stereo_roles(
        ch0_segs, ch1_segs, {"channel_0": "manager", "channel_1": "client"}
    )

    assert conf == "high"
    assert len(combined) == 2
    assert combined[0]["speaker_role"] == "manager"
    assert combined[1]["speaker_role"] == "client"


def test_mono_heuristic_role_classification():
    classifier = RoleClassifier()

    segments = [
        {
            "start": 0.0,
            "end": 4.0,
            "speaker_label": "SPEAKER_0",
            "text": "Здравствуйте! Компания Наружка, меня зовут Алексей.",
        },
        {
            "start": 4.5,
            "end": 8.0,
            "speaker_label": "SPEAKER_1",
            "text": "Добрый день, подскажите пожалуйста по ценам на щиты в Химках.",
        },
    ]

    classified, conf = classifier.classify_mono_speakers(segments)

    assert conf == "high"
    assert classified[0]["speaker_role"] == "manager"
    assert classified[1]["speaker_role"] == "client"


async def test_llm_refine_speaker_roles_mocked(monkeypatch):
    from app.llm.local_adapter import LocalLlamaCppAdapter

    adapter = LocalLlamaCppAdapter()

    segments = [
        {"start": 0.0, "end": 2.0, "speaker_label": "SPEAKER_1", "text": "Здравствуйте, компания APC."},
        {"start": 2.5, "end": 4.5, "speaker_label": "SPEAKER_0", "text": "Сколько стоит щит?"},
    ]

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": '{"roles": ["manager", "client"]}'
                        }
                    }
                ]
            }

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr("httpx.AsyncClient", FakeAsyncClient)

    refined = await adapter.refine_speaker_roles(segments)
    assert len(refined) == 2
    assert refined[0]["speaker_role"] == "manager"
    assert refined[0]["speaker_label"] == "SPEAKER_0"
    assert refined[1]["speaker_role"] == "client"
    assert refined[1]["speaker_label"] == "SPEAKER_1"


async def test_llm_refine_speaker_roles_fallback_on_error(monkeypatch):
    from app.llm.local_adapter import LocalLlamaCppAdapter

    adapter = LocalLlamaCppAdapter()
    segments = [
        {"start": 0.0, "end": 2.0, "speaker_label": "SPEAKER_0", "text": "Здравствуйте"},
    ]

    class BrokenAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, *args, **kwargs):
            raise RuntimeError("Connection refused")

    monkeypatch.setattr("httpx.AsyncClient", BrokenAsyncClient)

    # Should gracefully return original segments without exception
    result = await adapter.refine_speaker_roles(segments)
    assert len(result) == 1
    assert result[0]["text"] == "Здравствуйте"

