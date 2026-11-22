from typing import Any

import numpy as np
from sklearn.cluster import HDBSCAN, AgglomerativeClustering
from sklearn.feature_extraction.text import TfidfVectorizer

from app.analytics.embeddings import MAX_EMBEDDING_TEXT_CHARS, get_embedding_engine
from app.core.logging import get_logger

logger = get_logger("clustering_engine")

# Russian stop words for outdoor advertising queries
RU_STOP_WORDS = [
    "и",
    "в",
    "во",
    "не",
    "что",
    "он",
    "на",
    "я",
    "с",
    "со",
    "как",
    "а",
    "то",
    "все",
    "она",
    "так",
    "его",
    "но",
    "да",
    "ты",
    "к",
    "у",
    "же",
    "вы",
    "за",
    "бы",
    "по",
    "только",
    "ее",
    "мне",
    "было",
    "вот",
    "от",
    "меня",
    "еще",
    "нет",
    "о",
    "из",
    "ему",
    "теперь",
    "когда",
    "даже",
    "ну",
    "вдруг",
    "ли",
    "если",
    "уже",
    "или",
    "ни",
    "быть",
    "был",
    "него",
    "до",
    "вас",
    "нибудь",
    "опять",
    "уж",
    "вам",
    "ведь",
    "там",
    "потом",
    "себя",
    "ничего",
    "ей",
    "может",
    "они",
    "тут",
    "где",
    "есть",
    "надо",
    "ней",
    "для",
    "мы",
    "тебя",
    "их",
    "чем",
    "была",
    "сам",
    "чтоб",
    "без",
    "будто",
    "чего",
    "раз",
    "тоже",
    "себе",
    "под",
    "будет",
    "ж",
    "тогда",
    "кто",
    "этот",
    "того",
    "потому",
    "этого",
    "какой",
    "совсем",
    "ним",
    "здесь",
    "этом",
    "один",
    "почти",
    "мой",
    "тем",
    "чтобы",
    "нее",
    "сейчас",
    "были",
    "куда",
    "зачем",
    "всех",
    "никогда",
    "можно",
    "при",
    "наконец",
    "два",
    "об",
    "другой",
    "хоть",
    "после",
    "над",
    "больше",
    "тот",
    "через",
    "эти",
    "нас",
    "про",
    "всего",
    "них",
    "какая",
    "много",
    "разве",
    "три",
    "эту",
    "моя",
    "впрочем",
    "хорошо",
    "свою",
    "этой",
    "перед",
    "иногда",
    "лучше",
    "чуть",
    "том",
    "нельзя",
    "такой",
    "им",
    "более",
    "всегда",
    "конечно",
    "всю",
    "между",
    "пожалуйста",
    "нужно",
    "хотим",
    "хочу",
    "подскажите",
    "здравствуйте",
]


class TopicClusteringEngine:
    def __init__(
        self,
        min_cluster_size: int = 2,
        cosine_distance_threshold: float = 0.16,
        embedding_engine: Any | None = None,
    ):
        self.min_cluster_size = min_cluster_size
        self.cosine_distance_threshold = cosine_distance_threshold
        self._embedding_engine = embedding_engine
        self.vectorizer = TfidfVectorizer(
            stop_words=RU_STOP_WORDS,
            ngram_range=(1, 2),
            max_df=0.9,
            min_df=1,
            sublinear_tf=True,
        )

    def _embed_texts(self, texts: list[str]) -> np.ndarray | None:
        """Encodes texts with local multilingual-e5-small ONNX embeddings if available."""
        engine = (
            self._embedding_engine if self._embedding_engine is not None else get_embedding_engine()
        )
        if not engine.is_available():
            return None

        vectors: list[list[float]] = []
        for text in texts:
            truncated = (
                text[:MAX_EMBEDDING_TEXT_CHARS] if len(text) > MAX_EMBEDDING_TEXT_CHARS else text
            )
            vec = engine.embed_query_safe(truncated)
            if vec is None:
                return None
            vectors.append(vec)

        return np.asarray(vectors, dtype=np.float32)

    def _cluster_embeddings(self, dense_vectors: np.ndarray) -> np.ndarray:
        n_samples = dense_vectors.shape[0]
        has_zero_vectors = bool(np.any(np.linalg.norm(dense_vectors, axis=1) <= 1e-12))

        if has_zero_vectors:
            metric = "euclidean"
            threshold = float(np.sqrt(2.0 * self.cosine_distance_threshold))
        else:
            metric = "cosine"
            threshold = self.cosine_distance_threshold

        clusterer = AgglomerativeClustering(
            n_clusters=None,
            distance_threshold=threshold,
            metric=metric,
            linkage="average",
        )
        labels = clusterer.fit_predict(dense_vectors)

        # On larger batches, group multiple singletons into noise (-1) if real clusters exist
        if n_samples >= 6:
            unique, counts = np.unique(labels, return_counts=True)
            size_map = dict(zip(unique.tolist(), counts.tolist()))
            has_real_cluster = any(c >= self.min_cluster_size for c in size_map.values())
            small_clusters = [lbl for lbl, c in size_map.items() if c < self.min_cluster_size]
            if has_real_cluster and len(small_clusters) >= 2:
                small_set = set(small_clusters)
                labels = np.array([-1 if lbl in small_set else lbl for lbl in labels], dtype=int)

        return labels

    def fit_transform_texts(
        self,
        texts: list[str],
    ) -> tuple[list[int], dict[int, str], list[Any]]:
        """
        Embeds texts using local multilingual-e5-small ONNX embeddings (with TF-IDF fallback),
        performs semantic clustering, and generates descriptive topic labels via TF-IDF keywords.
        """
        if not texts:
            return [], {}, []

        if len(texts) < 2:
            return [0], {0: texts[0]}, [1.0]

        # Check if all texts are identical or whitespace
        cleaned_set = set(t.strip().lower() for t in texts if t.strip())
        if len(cleaned_set) <= 1:
            first_label = texts[0].strip() or "Общие запросы"
            return [0] * len(texts), {0: first_label[:40]}, [[1.0]] * len(texts)

        n_samples = len(texts)

        # 1. Primary path: local semantic embeddings (multilingual-e5-small-int8)
        emb_vectors = self._embed_texts(texts)
        if (
            emb_vectors is not None
            and emb_vectors.shape[0] == n_samples
            and emb_vectors.shape[1] > 0
        ):
            labels = self._cluster_embeddings(emb_vectors)
            cluster_labels = self._generate_cluster_labels(texts, labels)
            logger.info(
                "clustering_completed",
                mode="embeddings",
                n_samples=n_samples,
                n_clusters=len(cluster_labels),
            )
            return labels.tolist(), cluster_labels, emb_vectors.tolist()

        # 2. Fallback path: TF-IDF + HDBSCAN / AgglomerativeClustering (when embeddings are disabled/absent)
        try:
            # For small sets of texts (<= 5), do not filter with max_df=0.9
            vec = TfidfVectorizer(
                stop_words=RU_STOP_WORDS
                if any(w not in RU_STOP_WORDS for t in texts for w in t.lower().split())
                else None,
                ngram_range=(1, 2),
                max_df=0.9 if len(texts) > 5 else 1.0,
                min_df=1,
                sublinear_tf=True,
            )
            tfidf_matrix = vec.fit_transform(texts)
            dense_vectors = tfidf_matrix.toarray()
        except ValueError:
            # Fallback when vocabulary is empty (e.g. only stop words or identical terms)
            return (
                [0] * len(texts),
                {0: texts[0][:40] if texts else "Общие запросы"},
                [[1.0]] * len(texts),
            )

        if dense_vectors.shape[1] == 0:
            return [0] * n_samples, {0: texts[0][:40]}, [[1.0]] * n_samples

        if n_samples >= 6:
            clusterer = HDBSCAN(
                min_cluster_size=max(2, self.min_cluster_size),
                metric="euclidean",
                cluster_selection_epsilon=0.3,
            )
            labels = clusterer.fit_predict(dense_vectors)
        else:
            # Fallback for small batches
            n_clusters = min(n_samples, 2)
            has_zero_vectors = np.any(np.all(dense_vectors == 0, axis=1))
            metric = "euclidean" if has_zero_vectors else "cosine"
            clusterer = AgglomerativeClustering(
                n_clusters=n_clusters, metric=metric, linkage="average"
            )
            labels = clusterer.fit_predict(dense_vectors)

        # If all points were classified as noise (-1), group into single cluster
        if all(lbl == -1 for lbl in labels):
            labels = np.zeros(n_samples, dtype=int)

        # 3. Compute cluster representative labels via TF-IDF keywords
        cluster_labels = self._generate_cluster_labels(texts, labels)
        logger.info(
            "clustering_completed",
            mode="tfidf_fallback",
            n_samples=n_samples,
            n_clusters=len(cluster_labels),
        )

        return labels.tolist(), cluster_labels, dense_vectors.tolist()

    def _generate_cluster_labels(
        self,
        texts: list[str],
        labels: np.ndarray,
    ) -> dict[int, str]:
        unique_labels = set(labels)
        cluster_names: dict[int, str] = {}

        for lab in unique_labels:
            if lab == -1:
                cluster_names[lab] = "Разное (шумовые запросы)"
                continue

            # Gather all documents in cluster
            cluster_docs_indices = np.where(labels == lab)[0]
            if len(cluster_docs_indices) == 0:
                continue

            # Aggregate vectors
            cluster_texts = [texts[idx] for idx in cluster_docs_indices]
            sub_vec = TfidfVectorizer(stop_words=RU_STOP_WORDS, ngram_range=(1, 2))
            try:
                sub_mat = sub_vec.fit_transform(cluster_texts)
                sub_features = sub_vec.get_feature_names_out()
                sums = np.asarray(sub_mat.sum(axis=0)).flatten()
                top_indices = sums.argsort()[::-1][:3]
                top_keywords = [sub_features[i] for i in top_indices if sums[i] > 0]
                label_str = (
                    " / ".join(top_keywords).capitalize() if top_keywords else "Общая тематика"
                )
            except Exception:
                label_str = cluster_texts[0][:40]

            cluster_names[lab] = label_str

        return cluster_names
