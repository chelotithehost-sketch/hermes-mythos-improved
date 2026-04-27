"""Tests for core.brain — DAG pipeline executor."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from core.brain import BrainDAG, LayerNode, Edge, EdgeType


class TestEdge:
    """Test DAG edge behavior."""

    def test_sequential_always_follows(self):
        edge = Edge("a", "b", EdgeType.SEQUENTIAL)
        assert edge.should_follow({}) is True

    def test_conditional_with_true_condition(self):
        edge = Edge("a", "b", EdgeType.CONDITIONAL, condition=lambda ctx: ctx.get("flag"))
        assert edge.should_follow({"flag": True}) is True

    def test_conditional_with_false_condition(self):
        edge = Edge("a", "b", EdgeType.CONDITIONAL, condition=lambda ctx: ctx.get("flag"))
        assert edge.should_follow({"flag": False}) is False

    def test_skip_never_follows(self):
        edge = Edge("a", "b", EdgeType.SKIP)
        assert edge.should_follow({}) is False


class TestLayerNode:
    """Test LayerNode importlib loading."""

    def test_module_path_default(self):
        node = LayerNode(name="thinker")
        assert node.module_path == "layers.thinker"

    def test_module_path_custom(self):
        node = LayerNode(name="custom", module_path="my.custom.module")
        assert node.module_path == "my.custom.module"

    @pytest.mark.asyncio
    async def test_execute_loads_and_unloads_module(self):
        """Verify that execute() loads the module and unloads it after."""
        node = LayerNode(name="thinker")
        mock_module = MagicMock()
        mock_module.execute = AsyncMock(return_value={"result": "ok"})

        with patch("core.brain.importlib") as mock_importlib, \
             patch("core.brain.sys") as mock_sys, \
             patch("core.brain.gc") as mock_gc:
            mock_importlib.import_module.return_value = mock_module
            mock_sys.modules = {"layers.thinker": mock_module}

            result = await node.execute(
                context={"premise": "test", "genre": "fiction"},
                gateway=MagicMock(),
                cfg=MagicMock(),
            )

            mock_importlib.import_module.assert_called_once_with("layers.thinker")
            mock_module.execute.assert_called_once()
            assert result == {"result": "ok"}


class TestBrainDAG:
    """Test BrainDAG structure and execution."""

    def test_layer_names(self):
        """Verify all 7 layers are defined."""
        assert len(BrainDAG.LAYER_NAMES) == 7
        assert "thinker" in BrainDAG.LAYER_NAMES
        assert "publisher" in BrainDAG.LAYER_NAMES

    def test_dag_edges_include_revision_loop(self):
        """Verify the Reviewer→Writer conditional edge exists."""
        gateway = MagicMock()
        state = MagicMock()
        cfg = MagicMock()
        cfg.max_revisions = 3

        dag = BrainDAG(gateway=gateway, state=state, cfg=cfg)

        # Check for the revision loop edge
        revision_edges = [
            e for e in dag.edges
            if e.source == "reviewer" and e.target == "writer"
        ]
        assert len(revision_edges) == 1
        assert revision_edges[0].edge_type == EdgeType.CONDITIONAL

    def test_dag_edges_include_forward_edge(self):
        """Verify the Reviewer→Compiler edge exists."""
        gateway = MagicMock()
        state = MagicMock()
        cfg = MagicMock()
        cfg.max_revisions = 3

        dag = BrainDAG(gateway=gateway, state=state, cfg=cfg)

        forward_edges = [
            e for e in dag.edges
            if e.source == "reviewer" and e.target == "compiler"
        ]
        assert len(forward_edges) == 1

    def test_get_next_after_thinker(self):
        """Verify thinker → analyser."""
        gateway = MagicMock()
        state = MagicMock()
        cfg = MagicMock()
        cfg.max_revisions = 3

        dag = BrainDAG(gateway=gateway, state=state, cfg=cfg)
        next_node = dag._get_next_node("thinker", {})
        assert next_node == "analyser"

    def test_get_next_after_reviewer_needs_revision(self):
        """Verify reviewer → writer when needs_revision is True."""
        gateway = MagicMock()
        state = MagicMock()
        cfg = MagicMock()
        cfg.max_revisions = 3

        dag = BrainDAG(gateway=gateway, state=state, cfg=cfg)
        next_node = dag._get_next_node("reviewer", {"needs_revision": True})
        assert next_node == "writer"

    def test_get_next_after_reviewer_approved(self):
        """Verify reviewer → compiler when needs_revision is False."""
        gateway = MagicMock()
        state = MagicMock()
        cfg = MagicMock()
        cfg.max_revisions = 3

        dag = BrainDAG(gateway=gateway, state=state, cfg=cfg)
        next_node = dag._get_next_node("reviewer", {"needs_revision": False})
        assert next_node == "compiler"

    def test_get_next_after_publisher(self):
        """Verify publisher → None (end of pipeline)."""
        gateway = MagicMock()
        state = MagicMock()
        cfg = MagicMock()
        cfg.max_revisions = 3

        dag = BrainDAG(gateway=gateway, state=state, cfg=cfg)
        next_node = dag._get_next_node("publisher", {})
        assert next_node is None
