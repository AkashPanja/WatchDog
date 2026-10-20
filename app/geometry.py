"""Pure geometry helpers (no heavy dependencies)."""

from __future__ import annotations


def point_in_polygon(point: tuple[float, float], polygon: list[list[float]]) -> bool:
    """Ray casting test for a point in a polygon."""
    if len(polygon) < 3:
        return False
    x, y = point
    inside = False
    count = len(polygon)
    for index in range(count):
        x1, y1 = polygon[index]
        x2, y2 = polygon[(index + 1) % count]
        if (y1 > y) != (y2 > y):
            cross = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < cross:
                inside = not inside
    return inside


def bbox_zone_hit(
    bbox: tuple[float, float, float, float],
    polygon: list[list[float]],
    frame_width: int,
    frame_height: int,
) -> bool:
    """True when a bbox center or a bbox corner falls inside the zone."""
    if len(polygon) < 3:
        return True
    x1, y1, x2, y2 = bbox
    cx = (x1 + x2) / 2 / max(1, frame_width)
    cy = (y1 + y2) / 2 / max(1, frame_height)
    if point_in_polygon((cx, cy), polygon):
        return True
    for px, py in (
        (x1 / frame_width, y1 / frame_height),
        (x2 / frame_width, y1 / frame_height),
        (x1 / frame_width, y2 / frame_height),
        (x2 / frame_width, y2 / frame_height),
    ):
        if point_in_polygon((px, py), polygon):
            return True
    return False


def valid_polygon(polygon: list[list[float]], max_points: int = 50) -> bool:
    """Normalized polygon: 3..max_points points with 0-1 coordinates."""
    if polygon and len(polygon) < 3:
        return False
    if len(polygon) > max_points:
        return False
    for point in polygon:
        if len(point) != 2:
            return False
        if not all(isinstance(value, (int, float)) and 0.0 <= value <= 1.0 for value in point):
            return False
    return True
