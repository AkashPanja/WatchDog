"""Per-camera detection limits with environment fallbacks."""

from __future__ import annotations

from .config import config


def motion_limits(camera: dict) -> tuple[float, float]:
    """Return (threshold, cooldown) for a camera, falling back to env defaults.

    A camera row may override either value; ``None`` means "use the global
    setting". Values are clamped to sane ranges so a bad row cannot disable
    detection entirely.
    """
    threshold = camera.get("motion_threshold")
    cooldown = camera.get("motion_cooldown")
    if threshold is None:
        threshold = config.motion_threshold
    if cooldown is None:
        cooldown = config.motion_cooldown
    try:
        threshold_value = float(threshold)
    except (TypeError, ValueError):
        threshold_value = config.motion_threshold
    try:
        cooldown_value = float(cooldown)
    except (TypeError, ValueError):
        cooldown_value = config.motion_cooldown
    return (
        min(1.0, max(0.0001, threshold_value)),
        min(3600.0, max(0.0, cooldown_value)),
    )
