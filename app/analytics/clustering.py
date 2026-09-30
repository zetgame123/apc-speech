import numpy as np
from sklearn.cluster import HDBSCAN, AgglomerativeClustering
from sklearn.feature_extraction.text import TfidfVectorizer

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
    def __init__(self, min_cluster_size: int = 2):
        self.min_cluster_size = min_cluster_size
        self.vectorizer = TfidfVectorizer(
            stop_words=RU_STOP_WORDS,
            ngram_range=(1, 2),
            max_df=0.9,
            min_df=1,
            sublinear_tf=True,
        )

    def fit_transform_texts(
        self,
        texts: list[str],
    ) -> tuple[list[int], dict[int, str], list[float]]:
        """
        Embeds texts, performs clustering with HDBSCAN / Agglomerative fallback,
        and generates descriptive topic labels via class-based TF-IDF.
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

        # 1. Feature extraction with fallback for edge cases (e.g. max_df filtering out all words)
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

        n_samples = len(texts)

        if dense_vectors.shape[1] == 0:
            return [0] * n_samples, {0: texts[0][:40]}, [[1.0]] * n_samples

        # 2. Cluster model selection
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
            clusterer = AgglomerativeClustering(
                n_clusters=n_clusters, metric="cosine", linkage="average"
            )
            labels = clusterer.fit_predict(dense_vectors)

        # If all points were classified as noise (-1), group into single cluster
        if all(lbl == -1 for lbl in labels):
            labels = np.zeros(n_samples, dtype=int)

        # 3. Compute cluster representative labels via c-TF-IDF
        cluster_labels = self._generate_cluster_labels(texts, labels)

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
