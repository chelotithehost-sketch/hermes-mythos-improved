"""
Hermes-Mythos Brain — DAG-based pipeline executor with importlib layer loading.

Implements a real directed acyclic graph where:
- Each node is a LayerNode that importlib-loads a module from layers/
- Edges define execution order and conditional branching
- The Reviewer→Writer revision loop is a proper conditional edge
- Layer modules are loaded, executed, then unloaded to save memory
"""

from __future__ import annotations

import asyncio
import gc
import importlib
import logging
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from core.config import Config
from core.gateway import Gateway
from core.state import StateManager

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DAG Edge types
# ---------------------------------------------------------------------------

class EdgeType(str, Enum):
    """Types of edges in the execution DAG."""
    SEQUENTIAL = "sequential"      # Always follow this edge
    CONDITIONAL = "conditional"    # Follow only if condition is met
    SKIP = "skip"                  # Skip target, continue to next


@dataclass
class Edge:
    """An edge connecting two layer nodes in the DAG."""
    source: str
    target: str
    edge_type: EdgeType = EdgeType.SEQUENTIAL
    condition: Optional[Callable[[dict], bool]] = None  # For CONDITIONAL edges

    def should_follow(self, context: dict) -> bool:
        """Determine if this edge should be followed given current context."""
        if self.edge_type == EdgeType.SEQUENTIAL:
            return True
        if self.edge_type == EdgeType.CONDITIONAL:
            return self.condition(context) if self.condition else False
        if self.edge_type == EdgeType.SKIP:
            return False
        return False


# ---------------------------------------------------------------------------
# LayerNode — importlib-based module loading/unloading
# ---------------------------------------------------------------------------

@dataclass
class LayerNode:
    """A DAG node that importlib-loads a layer module, executes it, then unloads it.

    This is the key innovation: each layer is loaded into memory only during
    its execution, then deleted from sys.modules and garbage collected.
    This keeps peak memory usage low for the <2GB RAM budget.
    """

    name: str  # e.g. "thinker", "analyser"
    module_path: str = ""  # e.g. "layers.thinker"

    def __post_init__(self):
        if not self.module_path:
            self.module_path = f"layers.{self.name}"

    async def execute(self, context: dict, gateway: Gateway, cfg: Config, **kwargs) -> dict:
        """Load the layer module, call execute(), then unload it.

        Args:
            context: The shared pipeline context dict.
            gateway: LLM gateway for completions.
            cfg: Application configuration.

        Returns:
            Updated context dict.
        """
        logger.info("Loading layer module: %s", self.module_path)
        start_time = time.monotonic()

        try:
            # Import the layer module
            module = importlib.import_module(self.module_path)

            # Call the layer's execute function
            result = await module.execute(
                context=context,
                gateway=gateway,
                cfg=cfg,
                **kwargs,
            )

            duration = time.monotonic() - start_time
            logger.info("Layer %s completed in %.2fs", self.name, duration)

            return result

        except Exception as e:
            logger.error("Layer %s failed: %s", self.name, e, exc_info=True)
            raise
        finally:
            # Unload the module to free memory
            self._unload_module()

    def _unload_module(self) -> None:
        """Remove the layer module from sys.modules and force GC."""
        if self.module_path in sys.modules:
            del sys.modules[self.module_path]
            logger.debug("Unloaded module: %s", self.module_path)
        gc.collect()


# ---------------------------------------------------------------------------
# BrainDAG — the pipeline executor
# ---------------------------------------------------------------------------

class BrainDAG:
    """Directed acyclic graph executor for the 7-layer pipeline.

    Builds a DAG with proper edges (including the Reviewer→Writer conditional
    revision loop) and executes nodes in topological order, following
    conditional branches as needed.
    """

    # The canonical 7 layers
    LAYER_NAMES = (
        "thinker", "analyser", "planner", "writer",
        "reviewer", "compiler", "publisher",
    )

    def __init__(self, gateway: Gateway, state: StateManager, cfg: Config):
        self.gateway = gateway
        self.state = state
        self.cfg = cfg

        # Build nodes
        self.nodes: Dict[str, LayerNode] = {}
        for name in self.LAYER_NAMES:
            self.nodes[name] = LayerNode(name=name)

        # Build edges — this is where the DAG structure lives
        self.edges: List[Edge] = self._build_edges()

    def _build_edges(self) -> List[Edge]:
        """Construct the DAG edges including the Reviewer→Writer revision loop.

        The graph:
            thinker → analyser → planner → writer → reviewer
                                                       ↓ (needs revision)
                                                     writer ← reviewer
                                                       ↓ (approved)
                                                    compiler → publisher
        """
        edges = [
            # Linear pipeline
            Edge("thinker", "analyser", EdgeType.SEQUENTIAL),
            Edge("analyser", "planner", EdgeType.SEQUENTIAL),
            Edge("planner", "writer", EdgeType.SEQUENTIAL),
            Edge("writer", "reviewer", EdgeType.SEQUENTIAL),
            # After reviewer, either go back to writer or forward to compiler
            Edge("reviewer", "writer", EdgeType.CONDITIONAL,
                 condition=lambda ctx: ctx.get("needs_revision", False)),
            Edge("reviewer", "compiler", EdgeType.CONDITIONAL,
                 condition=lambda ctx: not ctx.get("needs_revision", False)),
            Edge("compiler", "publisher", EdgeType.SEQUENTIAL),
        ]
        return edges

    def _get_next_node(self, current: str, context: dict) -> Optional[str]:
        """Determine the next node to execute based on edges and context."""
        candidates = [e for e in self.edges if e.source == current]
        for edge in candidates:
            if edge.should_follow(context):
                return edge.target
        return None

    async def run(
        self,
        manuscript_id: str,
        run_id: str,
        initial_context: Optional[dict] = None,
    ) -> dict:
        """Execute the full pipeline DAG.

        Args:
            manuscript_id: The manuscript being generated.
            run_id: This pipeline run's ID.
            initial_context: Optional seed context (e.g. from resume).

        Returns:
            Final pipeline context.
        """
        context = initial_context or {}
        context["manuscript_id"] = manuscript_id
        context["run_id"] = run_id
        context["revision_count"] = 0
        context["needs_revision"] = False
        context["max_revisions"] = self.cfg.max_revisions

        current = "thinker"
        completed_layers: Set[str] = set()
        layer_states: Dict[str, str] = {}

        logger.info("Starting pipeline for manuscript %s", manuscript_id)

        while current is not None:
            if current in completed_layers and current != "writer":
                # Prevent infinite loops (writer can be re-entered for revisions)
                logger.warning("Layer %s already completed, skipping", current)
                current = self._get_next_node(current, context)
                continue

            node = self.nodes[current]
            layer_start = time.monotonic()
            started_at = datetime.now(timezone.utc).isoformat()

            # Update state
            self.state.update_run(run_id, current_layer=current, layer_states=layer_states)
            layer_states[current] = "running"

            try:
                context = await node.execute(
                    context=context,
                    gateway=self.gateway,
                    cfg=self.cfg,
                )

                duration = time.monotonic() - layer_start
                layer_states[current] = "completed"
                completed_layers.add(current)

                # Record completion
                self.state.record_layer_completion(
                    run_id=run_id,
                    layer_name=current,
                    output=str(context.get(f"{current}_output", ""))[:5000],
                    started_at=started_at,
                    duration_secs=duration,
                )

                # Track revision loop
                if current == "reviewer" and context.get("needs_revision"):
                    context["revision_count"] = context.get("revision_count", 0) + 1
                    if context["revision_count"] >= context["max_revisions"]:
                        logger.info("Max revisions reached, proceeding to compiler")
                        context["needs_revision"] = False
                        completed_layers.discard("reviewer")  # Allow reviewer to run again isn't needed, we skip it

                logger.info(
                    "Layer %s done (%.1fs), next: %s",
                    current, duration, self._get_next_node(current, context),
                )

            except Exception as e:
                duration = time.monotonic() - layer_start
                layer_states[current] = "failed"
                self.state.record_layer_completion(
                    run_id=run_id,
                    layer_name=current,
                    output=f"ERROR: {e}",
                    started_at=started_at,
                    duration_secs=duration,
                    status="failed",
                )
                self.state.update_run(run_id, status="failed", error=str(e))
                raise

            # Advance to next node
            current = self._get_next_node(current, context)

        # Pipeline complete
        self.state.update_run(
            run_id,
            status="completed",
            layer_states=layer_states,
        )
        logger.info("Pipeline completed for manuscript %s", manuscript_id)
        return context

    async def resume(
        self,
        manuscript_id: str,
        run_id: str,
    ) -> dict:
        """Resume a pipeline from its last checkpoint.

        Reconstructs context from completed layer outputs and narrative
        fragments, then continues from where it left off.
        """
        run = self.state.get_run(run_id)
        if run is None:
            raise ValueError(f"Run {run_id} not found")
        if run["status"] == "completed":
            raise ValueError(f"Run {run_id} already completed")

        # Rebuild context from completed layers
        completions = self.state.get_layer_completions(run_id)
        context: dict = {
            "manuscript_id": manuscript_id,
            "run_id": run_id,
            "revision_count": 0,
            "needs_revision": False,
            "max_revisions": self.cfg.max_revisions,
        }

        # Rebuild narrative from saved fragments
        fragments = self.state.get_fragments(manuscript_id)
        if fragments:
            context["narrative"] = "\n\n".join(
                f"## Chapter {f['chapter_num']}: {f['title']}\n{f['content']}"
                for f in fragments
            )
            context["completed_chapters"] = [f["chapter_num"] for f in fragments]
            context["chapter_titles"] = {
                f["chapter_num"]: f["title"] for f in fragments
            }
            logger.info(
                "Restored %d narrative fragments for manuscript %s",
                len(fragments), manuscript_id,
            )

        # Restore layer outputs from completions
        for comp in completions:
            if comp["status"] == "completed":
                context[f"{comp['layer_name']}_output"] = comp.get("output", "")

        # Determine where to resume from
        layer_states = run.get("layer_states", {})
        last_completed = None
        for name in self.LAYER_NAMES:
            if layer_states.get(name) == "completed":
                last_completed = name

        if last_completed is None:
            resume_from = "thinker"
        else:
            resume_from = self._get_next_node(last_completed, context)
            if resume_from is None:
                resume_from = last_completed

        logger.info("Resuming pipeline from layer: %s", resume_from)

        # Mark already-completed layers so the main loop skips them
        # We override run() behavior by starting from resume_from
        current = resume_from
        completed_layers: Set[str] = set()
        for name in self.LAYER_NAMES:
            if layer_states.get(name) == "completed":
                completed_layers.add(name)

        # Re-run from the resume point
        while current is not None:
            if current in completed_layers and current != "writer":
                current = self._get_next_node(current, context)
                continue

            node = self.nodes[current]
            layer_start = time.monotonic()
            started_at = datetime.now(timezone.utc).isoformat()

            self.state.update_run(run_id, current_layer=current, layer_states=layer_states)
            layer_states[current] = "running"

            try:
                context = await node.execute(
                    context=context,
                    gateway=self.gateway,
                    cfg=self.cfg,
                )
                duration = time.monotonic() - layer_start
                layer_states[current] = "completed"
                completed_layers.add(current)

                self.state.record_layer_completion(
                    run_id=run_id,
                    layer_name=current,
                    output=str(context.get(f"{current}_output", ""))[:5000],
                    started_at=started_at,
                    duration_secs=duration,
                )

                if current == "reviewer" and context.get("needs_revision"):
                    context["revision_count"] = context.get("revision_count", 0) + 1
                    if context["revision_count"] >= context["max_revisions"]:
                        context["needs_revision"] = False

            except Exception as e:
                duration = time.monotonic() - layer_start
                layer_states[current] = "failed"
                self.state.record_layer_completion(
                    run_id=run_id,
                    layer_name=current,
                    output=f"ERROR: {e}",
                    started_at=started_at,
                    duration_secs=duration,
                    status="failed",
                )
                self.state.update_run(run_id, status="failed", error=str(e))
                raise

            current = self._get_next_node(current, context)

        self.state.update_run(run_id, status="completed", layer_states=layer_states)
        logger.info("Resumed pipeline completed for manuscript %s", manuscript_id)
        return context
