"""Unit tests for semantic module."""

from unittest.mock import MagicMock, patch

import pytest

from tgsentinel.semantic import _try_import_model, load_interests, score_text


class TestTryImportModel:
    """Test model import functionality."""

    def test_try_import_model_no_env_var(self, monkeypatch):
        """Test that no model is loaded when EMBEDDINGS_MODEL is not set."""
        monkeypatch.delenv("EMBEDDINGS_MODEL", raising=False)

        result = _try_import_model()

        assert result is None

    @patch("tgsentinel.semantic.SentenceTransformer")
    def test_try_import_model_success(self, mock_transformer, monkeypatch):
        """Test successful model import."""
        monkeypatch.setenv("EMBEDDINGS_MODEL", "all-MiniLM-L6-v2")
        mock_model = MagicMock()
        mock_transformer.return_value = mock_model

        # Reset module state
        import tgsentinel.semantic as sem

        sem._model = None

        result = _try_import_model()

        assert result is not None
        mock_transformer.assert_called_once_with("all-MiniLM-L6-v2")

    def test_try_import_model_exception(self, monkeypatch, caplog):
        """Test that exceptions are caught and logged."""
        monkeypatch.setenv("EMBEDDINGS_MODEL", "invalid-model")

        # Reset module state
        import tgsentinel.semantic as sem

        sem._model = None

        result = _try_import_model()

        assert result is None
        assert "Embeddings disabled" in caplog.text


class TestLoadInterests:
    """Test interest loading functionality."""

    @patch("tgsentinel.semantic._model")
    def test_load_interests_with_model(self, mock_model):
        """Test loading interests when model is available."""
        import numpy as np

        import tgsentinel.semantic as sem

        # Setup mock model
        mock_model.encode = MagicMock(return_value=np.array([[0.1, 0.2], [0.3, 0.4]]))
        sem._model = mock_model
        sem._interest_vec = None

        interests = ["test topic 1", "test topic 2"]
        load_interests(interests)

        assert sem._interest_vec is not None
        mock_model.encode.assert_called_once()

    def test_load_interests_without_model(self, monkeypatch):
        """Test loading interests when model is not available."""
        monkeypatch.delenv("EMBEDDINGS_MODEL", raising=False)

        import tgsentinel.semantic as sem

        sem._model = None
        sem._interest_vec = None

        interests = ["test topic"]
        load_interests(interests)

        # Should not crash, just do nothing
        assert sem._interest_vec is None


class TestScoreText:
    """Test text scoring functionality."""

    @patch("tgsentinel.semantic._model")
    @patch("tgsentinel.semantic._interest_vec")
    def test_score_text_with_model(self, mock_interest_vec, mock_model):
        """Test scoring text when model is available."""
        import numpy as np

        import tgsentinel.semantic as sem

        # Setup mocks
        mock_model.encode = MagicMock(return_value=np.array([[0.5, 0.5]]))
        mock_interest_vec = np.array([0.5, 0.5])
        sem._model = mock_model
        sem._interest_vec = mock_interest_vec

        text = "This is a test message"
        score = score_text(text)

        assert score is not None
        assert isinstance(score, float)
        mock_model.encode.assert_called_once_with([text], normalize_embeddings=True)

    def test_score_text_no_model(self):
        """Test scoring text when model is not available."""
        import tgsentinel.semantic as sem

        sem._model = None
        sem._interest_vec = None

        score = score_text("Test message")

        assert score is None

    @patch("tgsentinel.semantic._model")
    @patch("tgsentinel.semantic._interest_vec")
    def test_score_text_empty_text(self, mock_interest_vec, mock_model):
        """Test scoring empty text."""
        import tgsentinel.semantic as sem

        sem._model = mock_model
        sem._interest_vec = mock_interest_vec

        score = score_text("")

        assert score is None

    @patch("tgsentinel.semantic._model")
    @patch("tgsentinel.semantic._interest_vec")
    def test_score_text_no_interest_vec(self, mock_interest_vec, mock_model):
        """Test scoring when interest vector is not set."""
        import tgsentinel.semantic as sem

        sem._model = mock_model
        sem._interest_vec = None

        score = score_text("Test message")

        assert score is None

    @patch("tgsentinel.semantic._model")
    def test_score_text_similarity_range(self, mock_model):
        """Test that similarity scores are in expected range."""
        import numpy as np

        import tgsentinel.semantic as sem

        # Setup mocks with normalized vectors
        mock_model.encode = MagicMock(return_value=np.array([[0.6, 0.8]]))
        sem._model = mock_model
        sem._interest_vec = np.array([0.6, 0.8])  # Same as text vector

        score = score_text("Test message")

        # Cosine similarity of identical normalized vectors should be ~1.0
        assert score is not None
        assert 0.95 <= score <= 1.05

    @patch("tgsentinel.semantic._model")
    def test_score_text_orthogonal_vectors(self, mock_model):
        """Test scoring with orthogonal vectors."""
        import numpy as np

        import tgsentinel.semantic as sem

        # Setup mocks with orthogonal vectors
        mock_model.encode = MagicMock(return_value=np.array([[1.0, 0.0]]))
        sem._model = mock_model
        sem._interest_vec = np.array([0.0, 1.0])

        score = score_text("Test message")

        # Cosine similarity of orthogonal vectors should be ~0
        assert score is not None
        assert -0.1 <= score <= 0.1
