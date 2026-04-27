"""Tests for core.state — SQLite state management with connection pooling."""

import pytest
from core.state import StateManager


@pytest.fixture
def state(tmp_path):
    """Create a fresh StateManager with a temp database."""
    db_path = str(tmp_path / "test.db")
    sm = StateManager(db_path=db_path)
    yield sm
    sm.close()


class TestManuscripts:
    """Test manuscript CRUD operations."""

    def test_create_manuscript(self, state):
        ms = state.create_manuscript("ms001", "Test Book", "fiction", "A test premise")
        assert ms["id"] == "ms001"
        assert ms["title"] == "Test Book"

    def test_get_manuscript(self, state):
        state.create_manuscript("ms001", "Test Book", "fiction", "A test premise")
        ms = state.get_manuscript("ms001")
        assert ms is not None
        assert ms["title"] == "Test Book"

    def test_get_nonexistent_manuscript(self, state):
        ms = state.get_manuscript("nonexistent")
        assert ms is None

    def test_list_manuscripts(self, state):
        state.create_manuscript("ms001", "Book 1", "fiction", "Premise 1")
        state.create_manuscript("ms002", "Book 2", "fantasy", "Premise 2")
        mss = state.list_manuscripts()
        assert len(mss) == 2

    def test_update_manuscript_status(self, state):
        state.create_manuscript("ms001", "Test", "fiction", "Premise")
        state.update_manuscript_status("ms001", "running")
        ms = state.get_manuscript("ms001")
        assert ms["status"] == "running"


class TestPipelineRuns:
    """Test pipeline run operations."""

    def test_create_run(self, state):
        state.create_manuscript("ms001", "Test", "fiction", "Premise")
        run = state.create_run("run001", "ms001")
        assert run["id"] == "run001"
        assert run["status"] == "running"

    def test_get_run(self, state):
        state.create_manuscript("ms001", "Test", "fiction", "Premise")
        state.create_run("run001", "ms001")
        run = state.get_run("run001")
        assert run is not None
        assert run["status"] == "running"

    def test_update_run_status(self, state):
        state.create_manuscript("ms001", "Test", "fiction", "Premise")
        state.create_run("run001", "ms001")
        state.update_run("run001", status="completed")
        run = state.get_run("run001")
        assert run["status"] == "completed"
        assert run["completed_at"] is not None

    def test_update_run_layer_states(self, state):
        state.create_manuscript("ms001", "Test", "fiction", "Premise")
        state.create_run("run001", "ms001")
        state.update_run("run001", layer_states={"thinker": "completed"})
        run = state.get_run("run001")
        assert run["layer_states"]["thinker"] == "completed"

    def test_get_latest_run(self, state):
        state.create_manuscript("ms001", "Test", "fiction", "Premise")
        state.create_run("run001", "ms001")
        state.create_run("run002", "ms001")
        run = state.get_latest_run("ms001")
        assert run["id"] == "run002"


class TestLayerCompletions:
    """Test layer completion recording."""

    def test_record_completion(self, state):
        state.create_manuscript("ms001", "Test", "fiction", "Premise")
        state.create_run("run001", "ms001")
        state.record_layer_completion(
            run_id="run001",
            layer_name="thinker",
            output="test output",
            started_at="2024-01-01T00:00:00",
            duration_secs=1.5,
        )
        completions = state.get_layer_completions("run001")
        assert len(completions) == 1
        assert completions[0]["layer_name"] == "thinker"


class TestNarrativeFragments:
    """Test narrative fragment storage."""

    def test_save_and_get_fragments(self, state):
        state.create_manuscript("ms001", "Test", "fiction", "Premise")
        state.create_run("run001", "ms001")
        state.save_fragment("ms001", "run001", 1, "Chapter 1", "Once upon a time...")
        state.save_fragment("ms001", "run001", 2, "Chapter 2", "The journey continued...")
        fragments = state.get_fragments("ms001")
        assert len(fragments) == 2
        assert fragments[0]["chapter_num"] == 1
        assert fragments[1]["chapter_num"] == 2

    def test_fragment_count(self, state):
        state.create_manuscript("ms001", "Test", "fiction", "Premise")
        state.create_run("run001", "ms001")
        state.save_fragment("ms001", "run001", 1, "Ch1", "Text")
        assert state.get_fragment_count("ms001") == 1

    def test_word_count_computed(self, state):
        state.create_manuscript("ms001", "Test", "fiction", "Premise")
        state.create_run("run001", "ms001")
        state.save_fragment("ms001", "run001", 1, "Ch1", "one two three four five")
        fragments = state.get_fragments("ms001")
        assert fragments[0]["word_count"] == 5
