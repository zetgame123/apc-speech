from app.analytics.clustering import TopicClusteringEngine


def test_clustering_engine_with_sample_queries():
    engine = TopicClusteringEngine(min_cluster_size=2)

    queries = [
        "щиты три на шесть в Балашихе",
        "билборд 3х6 Балашиха шоссе",
        "суперсайт на МКАД свободные места",
        "суперсайт щит большой формат трасса",
        "сколько стоит печать баннера",
        "прайс на монтаж и печать постера",
    ]

    labels, cluster_names, vectors = engine.fit_transform_texts(queries)

    assert len(labels) == len(queries)
    assert len(cluster_names) >= 2
    assert len(vectors) == len(queries)
    # Check that labels are valid integers
    for lbl in labels:
        assert isinstance(lbl, int)


def test_clustering_single_query():
    engine = TopicClusteringEngine()
    labels, cluster_names, _vectors = engine.fit_transform_texts(["одиночный запрос"])

    assert labels == [0]
    assert len(cluster_names) == 1


def test_clustering_identical_and_edge_queries():
    engine = TopicClusteringEngine()
    # Identical queries (A18 check)
    labels, cluster_names, _vectors = engine.fit_transform_texts(["щит", "щит"])
    assert len(labels) == 2
    assert len(cluster_names) >= 1
    assert labels[0] == labels[1]

    # Only stop words
    labels_sw, cluster_names_sw, _ = engine.fit_transform_texts(["и в не", "что он на"])
    assert len(labels_sw) == 2
    assert len(cluster_names_sw) >= 1


class _FakeEmbeddingEngine:
    def __init__(self, available: bool = True, mapping: dict[str, list[float]] | None = None):
        self._available = available
        self._mapping = mapping or {}

    def is_available(self) -> bool:
        return self._available

    def embed_query_safe(self, text: str) -> list[float] | None:
        if not self._available:
            return None
        return self._mapping.get(text)


def test_clustering_semantic_embeddings_groups_synonyms():
    # Synonyms with zero lexical overlap should cluster together via embeddings
    mapping = {
        "щиты в Балашихе": [1.0, 0.0, 0.0],
        "билборды на шоссе": [0.98, 0.199, 0.0],
        "стоимость печати": [0.0, 1.0, 0.0],
        "прайс на баннер": [0.199, 0.98, 0.0],
        "закрывающие документы": [0.0, 0.0, 1.0],
    }
    engine = TopicClusteringEngine(
        min_cluster_size=2,
        cosine_distance_threshold=0.16,
        embedding_engine=_FakeEmbeddingEngine(available=True, mapping=mapping),
    )

    queries = list(mapping.keys())
    labels, cluster_names, vectors = engine.fit_transform_texts(queries)

    assert len(labels) == 5
    assert len(vectors) == 5
    assert labels[0] == labels[1]
    assert labels[2] == labels[3]
    assert labels[0] != labels[2]
    assert labels[4] != labels[0] and labels[4] != labels[2]
    assert len(cluster_names) == 3


def test_clustering_falls_back_to_tfidf_when_embeddings_unavailable():
    engine = TopicClusteringEngine(
        min_cluster_size=2,
        embedding_engine=_FakeEmbeddingEngine(available=False),
    )
    queries = [
        "щит 3х6 Балашиха",
        "щит 3х6 Москва",
        "печать баннера цена",
        "печать баннера сроки",
    ]
    labels, cluster_names, vectors = engine.fit_transform_texts(queries)

    assert len(labels) == 4
    assert len(cluster_names) >= 2
    assert len(vectors) == 4
