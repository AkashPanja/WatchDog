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


def _orientation(
    a: list[float], b: list[float], c: list[float]
) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _proper_intersection(
    a: list[float], b: list[float], c: list[float], d: list[float]
) -> bool:
    """True when two segments cross at a single interior point."""
    if a == b or c == d:
        return False
    epsilon = 1e-12
    o1 = _orientation(a, b, c)
    o2 = _orientation(a, b, d)
    o3 = _orientation(c, d, a)
    o4 = _orientation(c, d, b)
    return (
        (o1 > epsilon or o1 < -epsilon)
        and (o2 > epsilon or o2 < -epsilon)
        and (o3 > epsilon or o3 < -epsilon)
        and (o4 > epsilon or o4 < -epsilon)
        and (o1 > 0) != (o2 > 0)
        and (o3 > 0) != (o4 > 0)
    )


def polygon_self_intersects(polygon: list[list[float]]) -> bool:
    """True when a polygon's edges cross (bow-tie / invalid zone)."""
    count = len(polygon)
    if count < 4:
        return False
    for i in range(count):
        for j in range(i + 1, count):
            if j == i + 1 or (i == 0 and j == count - 1):
                continue
            if _proper_intersection(
                polygon[i],
                polygon[(i + 1) % count],
                polygon[j],
                polygon[(j + 1) % count],
            ):
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


def face_rank(bbox: tuple[float, float, float, float], score: float) -> float:
    """Rank faces by detector confidence times face area (bigger & clearer wins)."""
    width = max(0.0, float(bbox[2]) - float(bbox[0]))
    height = max(0.0, float(bbox[3]) - float(bbox[1]))
    return width * height * max(0.0, float(score))
