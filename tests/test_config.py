"""Tests for core.config — configuration loading and validation."""

import os
import pytest
from unittest.mock import patch

from core.config import Config, load_config, FRONTIER_PROVIDERS, ALL_PROVIDERS


class TestConfigDefaults:
    """Test default configuration values."""

    def test_default_ollama_url(self):
        cfg = Config()
        assert cfg.ollama_base_url == "http://localhost:11434"

    def test_default_max_revisions(self):
        cfg = Config()
        assert cfg.max_revisions == 3

    def test_default_chapter_count(self):
        cfg = Config()
        assert cfg.chapter_count == 12

    def test_default_port(self):
        cfg = Config()
        assert cfg.port == 8000

    def test_frozen_config(self):
        cfg = Config()
        with pytest.raises(AttributeError):
            cfg.port = 9999


class TestAvailableProviders:
    """Test provider availability detection."""

    def test_no_keys_only_ollama(self):
        cfg = Config()
        avail = cfg.available_providers
        assert "ollama" in avail
        assert "openai" not in avail

    def test_openai_available(self):
        cfg = Config(openai_api_key="sk-test")
        assert "openai" in cfg.available_providers

    def test_anthropic_available(self):
        cfg = Config(anthropic_api_key="sk-ant-test")
        assert "anthropic" in cfg.available_providers

    def test_all_frontier_available(self):
        cfg = Config(
            openai_api_key="sk-test",
            anthropic_api_key="sk-ant-test",
            gemini_api_key="gemini-test",
        )
        avail = cfg.available_providers
        for p in FRONTIER_PROVIDERS:
            assert p in avail


class TestFallbackChain:
    """Test fallback chain filtering."""

    def test_active_chain_filters_unavailable(self):
        cfg = Config(openai_api_key="sk-test")
        chain = cfg.active_fallback_chain
        assert "openai" in chain
        assert "ollama" in chain
        # anthropic/gemini should be filtered out
        assert "anthropic" not in chain
        assert "gemini" not in chain

    def test_model_for_provider(self):
        cfg = Config()
        assert cfg.model_for("openai") == "gpt-4o"
        assert cfg.model_for("anthropic") == "claude-sonnet-4-20250514"
        assert cfg.model_for("unknown") == "gpt-4o"  # default fallback


class TestLoadConfig:
    """Test config loading from environment."""

    def test_load_config_creates_data_dir(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        with patch.dict(os.environ, {"DB_PATH": db_path, "DATA_DIR": str(tmp_path)}):
            cfg = load_config()
            assert cfg.db_path == db_path
            assert tmp_path.exists()

    def test_validation_rejects_bad_revisions(self):
        with pytest.raises(ValueError, match="max_revisions"):
            cfg = Config(max_revisions=-1)
            from core.config import _validate
            _validate(cfg)

    def test_validation_rejects_bad_chapter_count(self):
        with pytest.raises(ValueError, match="chapter_count"):
            cfg = Config(chapter_count=0)
            from core.config import _validate
            _validate(cfg)
