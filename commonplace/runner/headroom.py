"""Local resource probes and durable, short-lived launch reservations."""

import math
import os
import sqlite3
import time

RESERVE_MB = 512
RESERVE_SECONDS = 120
LEGACY_MAX_AGENTS = 20


def required(command):
    """Only an explicit expanded limit enables the additional startup guard."""
    limit = command.get("max_agents")
    return type(limit) is int and LEGACY_MAX_AGENTS < limit <= 100


def cpu_free():
    """Idle core equivalents from the one-minute load, or unknown on unsupported hosts."""
    try:
        cores = os.cpu_count()
        return max(0.0, cores - os.getloadavg()[0]) if cores else None
    except (OSError, AttributeError):
        return None


def snapshot(config):
    from .agent import DEFAULT_MIN_FREE_MEMORY_MB, _available_memory_mb

    return {
        "memory_available_mb": _available_memory_mb(),
        "cpu_free": cpu_free(),
        "memory_floor_mb": config.get("min_free_memory_mb", DEFAULT_MIN_FREE_MEMORY_MB),
    }


def validate(data):
    if not isinstance(data, dict) or set(data) != {
        "memory_available_mb",
        "cpu_free",
        "memory_floor_mb",
    }:
        raise ValueError("invalid resource report")
    for key in ("memory_available_mb", "memory_floor_mb"):
        value = data[key]
        if value is None and key == "memory_available_mb":
            continue
        if type(value) is not int or not 0 <= value <= 1 << 40:
            raise ValueError("invalid resource report")
    cpu = data["cpu_free"]
    if cpu is not None and (
        type(cpu) not in (int, float) or not math.isfinite(cpu) or not 0 <= cpu <= 1 << 20
    ):
        raise ValueError("invalid resource report")
    return data


def slots(data):
    """Known headroom limits; unknown probes do not manufacture a refusal."""
    result = []
    memory, floor = data["memory_available_mb"], data["memory_floor_mb"]
    if memory is not None and floor:
        result.append(max(0, (memory - floor) // RESERVE_MB))
    if data["cpu_free"] is not None:
        result.append(int(data["cpu_free"]))
    return result


def reserve(config, journal, key):
    """Serialize launch bursts across processes sharing the runner's private journal.

    Reserve before the launch, including unknown outcomes. Expiry lets real probes
    take over after startup; this is admission headroom, not a process memory limit.
    """
    if not slots(snapshot(config)):
        return None  # No measurements: do not require reservation storage either.
    try:
        with sqlite3.connect(journal / "headroom.sqlite3", timeout=2) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS reservations (id TEXT PRIMARY KEY, at REAL)")
            conn.execute("BEGIN IMMEDIATE")
            now = time.time()
            conn.execute("DELETE FROM reservations WHERE at<=?", (now - RESERVE_SECONDS,))
            pending = conn.execute("SELECT COUNT(*) FROM reservations").fetchone()[0]
            data = snapshot(config)
            memory, floor = data["memory_available_mb"], data["memory_floor_mb"]
            if memory is not None and floor and memory - pending * RESERVE_MB < floor + RESERVE_MB:
                return "low_memory"
            if data["cpu_free"] is not None and data["cpu_free"] - pending < 1:
                return "low_cpu"
            conn.execute("INSERT OR REPLACE INTO reservations VALUES (?,?)", (key, now))
    except (OSError, sqlite3.Error):
        return "busy"  # unavailable accounting must never permit an unguarded burst
    return None
