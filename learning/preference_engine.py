"""
Preference Engine — Feedback-based learning

Phase 1 (NOW): TF-IDF + Cosine Similarity
  - Convert job descriptions → TF-IDF vectors
  - Maintain liked_centroid and disliked_centroid
  - Score: sim(job, liked) - sim(job, disliked)

Phase 2 (after ~50+ feedback): Logistic Regression
  - Train on: TF-IDF vectors
  - Output: probability of "like"

The hybrid scoring formula:
  final_score = 0.6 * llm_score + 0.3 * embedding_sim + 0.1 * rule_score
"""

import json
import pickle
import traceback
import numpy as np
from typing import Optional

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.linear_model import LogisticRegression

from sqlalchemy.orm import Session
from db.crud import get_liked_feedback, get_disliked_feedback, get_feedback_counts
from db.crud import get_agent_state, set_agent_state


# Minimum feedback events before embedding scoring kicks in
MIN_FEEDBACK_FOR_SIMILARITY = 3
# Minimum feedback events before training a classifier
MIN_FEEDBACK_FOR_CLASSIFIER = 50

# TF-IDF settings
TFIDF_MAX_FEATURES = 3000
TFIDF_NGRAM_RANGE = (1, 2)


class PreferenceEngine:
    """
    Manages preference learning from feedback data.
    Persists TF-IDF vectorizer and centroids in the agent_state table.
    """

    def __init__(self, db: Session):
        self.db = db
        self.vectorizer: Optional[TfidfVectorizer] = None
        self.liked_centroid: Optional[np.ndarray] = None
        self.disliked_centroid: Optional[np.ndarray] = None
        self.classifier: Optional[LogisticRegression] = None
        self._loaded = False

    def build_profile(self) -> dict:
        """
        Build the preference profile from feedback history.
        Returns context dict for LLM scoring prompt.
        """
        counts = get_feedback_counts(self.db)
        liked = get_liked_feedback(self.db, limit=100)
        disliked = get_disliked_feedback(self.db, limit=100)

        # Build few-shot examples for LLM prompt
        liked_examples = [{
            "title": fb.title,
            "company": fb.company,
            "location": fb.location,
        } for fb in liked[:8]]

        disliked_examples = [{
            "title": fb.title,
            "company": fb.company,
            "location": fb.location,
            "reason": fb.red_flags,
        } for fb in disliked[:8]]

        return {
            "total_feedback": counts["total"],
            "liked_count": counts["liked"],
            "disliked_count": counts["disliked"],
            "liked_examples": liked_examples,
            "disliked_examples": disliked_examples,
        }

    def compute_embedding_score(self, job_description: str) -> float:
        """
        Compute embedding similarity score for a job description.
        Uses TF-IDF + cosine similarity against liked/disliked centroids.

        Returns: score in range [-1.0, 1.0], normalized to [0, 100]
        """
        counts = get_feedback_counts(self.db)
        if counts["total"] < MIN_FEEDBACK_FOR_SIMILARITY:
            return 50.0  # Neutral: not enough data

        try:
            self._ensure_model_built()

            if self.vectorizer is None or self.liked_centroid is None:
                return 50.0

            # Transform the new job
            job_vec = self.vectorizer.transform([job_description])

            # Compute similarities
            liked_sim = cosine_similarity(job_vec, self.liked_centroid.reshape(1, -1))[0][0]

            disliked_sim = 0.0
            if self.disliked_centroid is not None:
                disliked_sim = cosine_similarity(
                    job_vec, self.disliked_centroid.reshape(1, -1)
                )[0][0]

            # Raw score: positive means more like liked, negative means more like disliked
            raw_score = liked_sim - disliked_sim

            # Normalize to 0-100 range
            # raw_score is typically in [-1, 1], map to [0, 100]
            normalized = (raw_score + 1) * 50

            return max(0.0, min(100.0, normalized))

        except Exception as e:
            print(f"  [PREFERENCE] Embedding score error: {e}")
            traceback.print_exc()
            return 50.0

    def compute_classifier_score(self, job_description: str) -> Optional[float]:
        """
        Phase 2: Use trained logistic regression to predict like probability.
        Returns probability of "like" (0-100), or None if classifier not ready.
        """
        counts = get_feedback_counts(self.db)
        if counts["total"] < MIN_FEEDBACK_FOR_CLASSIFIER:
            return None

        try:
            self._ensure_model_built()

            if self.classifier is None or self.vectorizer is None:
                return None

            job_vec = self.vectorizer.transform([job_description])
            proba = self.classifier.predict_proba(job_vec)[0]

            # Index 1 = probability of class "1" (liked)
            like_prob = proba[1] if len(proba) > 1 else proba[0]
            return like_prob * 100

        except Exception as e:
            print(f"  [PREFERENCE] Classifier score error: {e}")
            return None

    def _ensure_model_built(self):
        """Build or load TF-IDF model and centroids from feedback data."""
        if self._loaded:
            return

        liked = get_liked_feedback(self.db, limit=200)
        disliked = get_disliked_feedback(self.db, limit=200)

        all_feedback = liked + disliked
        if len(all_feedback) < MIN_FEEDBACK_FOR_SIMILARITY:
            self._loaded = True
            return

        # Build corpus
        texts = []
        labels = []
        for fb in all_feedback:
            text = _build_training_text(fb)
            if text:
                texts.append(text)
                labels.append(fb.label)

        if len(texts) < MIN_FEEDBACK_FOR_SIMILARITY:
            self._loaded = True
            return

        # Fit TF-IDF
        self.vectorizer = TfidfVectorizer(
            max_features=TFIDF_MAX_FEATURES,
            ngram_range=TFIDF_NGRAM_RANGE,
            stop_words="english",
        )
        tfidf_matrix = self.vectorizer.fit_transform(texts)

        # Compute centroids
        liked_indices = [i for i, l in enumerate(labels) if l == 1]
        disliked_indices = [i for i, l in enumerate(labels) if l == 0]

        if liked_indices:
            liked_vecs = tfidf_matrix[liked_indices].toarray()
            self.liked_centroid = np.mean(liked_vecs, axis=0)

        if disliked_indices:
            disliked_vecs = tfidf_matrix[disliked_indices].toarray()
            self.disliked_centroid = np.mean(disliked_vecs, axis=0)

        # Phase 2: Train classifier if enough data
        counts = get_feedback_counts(self.db)
        if counts["total"] >= MIN_FEEDBACK_FOR_CLASSIFIER and len(set(labels)) > 1:
            try:
                self.classifier = LogisticRegression(
                    max_iter=1000,
                    class_weight="balanced",
                )
                self.classifier.fit(tfidf_matrix, labels)
                print(f"  [PREFERENCE] Classifier trained on {len(texts)} samples")
            except Exception as e:
                print(f"  [PREFERENCE] Classifier training failed: {e}")
                self.classifier = None

        self._loaded = True
        print(f"  [PREFERENCE] Model built: {len(liked_indices)} liked, "
              f"{len(disliked_indices)} disliked")

    def get_learning_stats(self) -> dict:
        """Get stats about the preference learning system."""
        counts = get_feedback_counts(self.db)
        return {
            "total_feedback": counts["total"],
            "liked": counts["liked"],
            "disliked": counts["disliked"],
            "similarity_active": counts["total"] >= MIN_FEEDBACK_FOR_SIMILARITY,
            "classifier_active": (
                counts["total"] >= MIN_FEEDBACK_FOR_CLASSIFIER
                and self.classifier is not None
            ),
            "min_for_similarity": MIN_FEEDBACK_FOR_SIMILARITY,
            "min_for_classifier": MIN_FEEDBACK_FOR_CLASSIFIER,
        }


def _build_training_text(feedback) -> str:
    """Build a combined text string from feedback data for TF-IDF training."""
    parts = []
    if feedback.title:
        # Weight the title more by repeating it
        parts.append(feedback.title)
        parts.append(feedback.title)
    if feedback.company:
        parts.append(feedback.company)
    if feedback.description:
        # Truncate to keep TF-IDF manageable
        parts.append(feedback.description[:2000])
    return " ".join(parts).strip()


def compute_hybrid_score(
    llm_score: float,
    embedding_score: float,
    rule_score: float,
    w_llm: float = 0.6,
    w_embed: float = 0.3,
    w_rule: float = 0.1,
) -> float:
    """
    Compute the final hybrid score.

    All input scores should be on 0-100 scale.
    Returns a score on 0-100 scale.
    """
    return (w_llm * llm_score) + (w_embed * embedding_score) + (w_rule * rule_score)
