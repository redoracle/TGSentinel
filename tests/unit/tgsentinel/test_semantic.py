"""Unit tests for semantic module."""

from unittest.mock import MagicMock, patch

import pytest

from tgsentinel.semantic import _try_import_model


@pytest.mark.unit
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
