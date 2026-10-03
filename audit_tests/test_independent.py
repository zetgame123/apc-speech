"""Independent acceptance probes; failures deliberately document baseline defects."""

import pytest

from app.analytics.clustering import TopicClusteringEngine
from app.asr.normalizer import RussianTextNormalizer
from app.core.errors import AppError
from app.core.security import sign_callback_payload, validate_ssrf_url
from app.llm.local_adapter import LocalLlamaCppAdapter
from app.llm.port import AnalysisContext, validate_analysis_schema
from app.metrics.speech_metrics import SpeechMetricsCalculator
from app.pii.masker import PiiMasker


@pytest.mark.parametrize(
    "source,expected",
    [
        ("три на шесть", "3×6"),
        ("шесть на три", "6×3"),
        ("пятнадцать секунд", "15 с"),
        ("двадцать пять тысяч рублей", "25 000 руб."),
        ("тринадцатого октября", "13 октября"),
        ("в Балашихе", "в Балашихе"),
        ("в Химках", "в Химках"),
        ("пришлите счет на оплату", "пришлите счет на оплату"),
        (
            "номер телефона плюс семь девятьсот двенадцать триста сорок пять шестьдесят семь восемьдесят девять",
            "номер телефона +7 912 345 67 89",
        ),
    ],
)
def test_normalization(source, expected):
    assert RussianTextNormalizer().normalize(source) == expected


@pytest.mark.parametrize(
    "source",
    [
        "Телефон +7 (912) 345-67-89",
        "Почта client@example.test",
        "Карта 4111 1111 1111 1111",
        "Паспорт 45 08 123456",
        "Карта 3782 822463 10005",
        "Паспорт 4508123456",
        "Телефон 8.912.345.67.89",
        "Телефон восемь девятьсот двенадцать триста сорок пять шестьдесят семь восемьдесят девять",
    ],
)
def test_pii_edge_cases(source):
    masked = PiiMasker().mask_text(source)
    assert masked != source


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost",
        "http://127.0.0.1",
        "http://0.0.0.0",
        "http://[::1]",
        "http://169.254.169.254",
        "http://10.1.2.3",
        "http://172.20.0.2",
        "http://192.168.1.2",
        "http://[::ffff:127.0.0.1]",
        "file:///etc/passwd",
        "ftp://example.com",
        "http://2130706433",
        "http://0177.0.0.1",
    ],
)
def test_ssrf_known_local_addresses(url):
    with pytest.raises(AppError):
        validate_ssrf_url(url)


@pytest.mark.parametrize("texts", [["", "щит"], ["а", "я", "щит"], [" ", " "], ["щит", "щит"], []])
def test_clustering_edges(texts):
    labels, names, vectors = TopicClusteringEngine().fit_transform_texts(texts)
    assert len(labels) == len(texts)


def analysis(text):
    return (
        LocalLlamaCppAdapter()
        ._analyze_via_rules(text, AnalysisContext(namespace="audit", call_id="audit"))
        .model_dump()
    )


def with_metrics(value):
    value["deterministic_speech_metrics"] = SpeechMetricsCalculator().compute([], 0)
    return value


def test_schema_rejects_unexpected_properties():
    a = with_metrics(analysis("[manager] Здравствуйте!"))
    validate_analysis_schema(a)
    a["unexpected"] = "not allowed"
    with pytest.raises(Exception):
        validate_analysis_schema(a)


def test_taxonomy_config_can_add_category():
    adapter = LocalLlamaCppAdapter()
    adapter.taxonomy = {"audit_category": {"keywords": ["новотаксономия"]}}
    a = adapter._analyze_via_rules(
        "[client] новотаксономия", AnalysisContext(namespace="audit", call_id="audit")
    ).model_dump()
    assert a["inquiry_type"] == "audit_category"
    validate_analysis_schema(with_metrics(a))


def test_checklist_only_credits_manager():
    a = analysis(
        "[client] Здравствуйте! Какой формат вам нужен? Могу предложить варианты. Отправлю предложение завтра. Оставьте телефон.\n[manager] Нет."
    )
    assert not a["manager_checklist"]["greeting"]
    assert not a["manager_checklist"]["next_step_named"]


def test_city_declension_extraction():
    a = analysis("[client] Нужны щиты в Балашихе и Химках.")
    assert set(a["entities"]["cities"]) == {"Балашиха", "Химки"}


def test_callback_formula():
    import hashlib
    import hmac

    body = '{"тест":"значение"}'.encode()
    ts, sig = sign_callback_payload("audit-secret", body)
    assert (
        sig
        == "sha256="
        + hmac.new(b"audit-secret", str(ts).encode() + b"." + body, hashlib.sha256).hexdigest()
    )
