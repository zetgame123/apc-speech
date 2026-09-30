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
