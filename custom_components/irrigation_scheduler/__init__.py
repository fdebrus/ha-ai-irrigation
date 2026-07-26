"""
The Irrigation Scheduler integration.

Scheduling logic is pure and lives in models.py / planner.py. I/O is split into
drivers.py (valve/distributor/button), scheduler.py (the tick, run/stop, Store),
coordinator.py (forecast) and ai.py (the nightly plan). This module wires them
onto the config entry; it is filled in once the scheduler exists (Phase 3).
"""

from __future__ import annotations
