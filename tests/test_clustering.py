import pytest
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
    for l in labels:
        assert isinstance(l, int)


def test_clustering_single_query():
    engine = TopicClusteringEngine()
    labels, cluster_names, vectors = engine.fit_transform_texts(["одиночный запрос"])

    assert labels == [0]
    assert len(cluster_names) == 1
