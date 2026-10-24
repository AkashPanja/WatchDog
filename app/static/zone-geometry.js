/* Pure zone geometry helpers (shared by the UI and Node tests). */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.ZoneGeometry = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  const EPS = 1e-12;

  function clamp(value, low, high) {
    return Math.min(high, Math.max(low, value));
  }

  function snapPoint(point, step) {
    const size = step || 0.01;
    return [
      clamp(Math.round(point[0] / size) * size, 0, 1),
      clamp(Math.round(point[1] / size) * size, 0, 1),
    ];
  }

  function polygonArea(points) {
    if (!points || points.length < 3) return 0;
    let total = 0;
    for (let i = 0; i < points.length; i += 1) {
      const [x1, y1] = points[i];
      const [x2, y2] = points[(i + 1) % points.length];
      total += x1 * y2 - x2 * y1;
    }
    return Math.abs(total) / 2;
  }

  function orient(a, b, c) {
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]);
  }

  function samePoint(a, b) {
    return a[0] === b[0] && a[1] === b[1];
  }

  function properIntersection(a, b, c, d) {
    if (samePoint(a, b) || samePoint(c, d)) return false;
    const o1 = orient(a, b, c);
    const o2 = orient(a, b, d);
    const o3 = orient(c, d, a);
    const o4 = orient(c, d, b);
    return (
      ((o1 > EPS && o2 < -EPS) || (o1 < -EPS && o2 > EPS)) &&
      ((o3 > EPS && o4 < -EPS) || (o3 < -EPS && o4 > EPS))
    );
  }

  function polygonSelfIntersects(points) {
    const count = points ? points.length : 0;
    if (count < 4) return false;
    for (let i = 0; i < count; i += 1) {
      for (let j = i + 1; j < count; j += 1) {
        if (j === i + 1) continue;
        if (i === 0 && j === count - 1) continue;
        const a = points[i];
        const b = points[(i + 1) % count];
        const c = points[j];
        const d = points[(j + 1) % count];
        if (properIntersection(a, b, c, d)) return true;
      }
    }
    return false;
  }

  function distanceToSegment(point, a, b) {
    const dx = b[0] - a[0];
    const dy = b[1] - a[1];
    const lengthSq = dx * dx + dy * dy;
    if (lengthSq === 0) {
      return { distance: Math.hypot(point[0] - a[0], point[1] - a[1]), point: [a[0], a[1]], t: 0 };
    }
    const t = clamp(
      ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / lengthSq,
      0,
      1
    );
    const projected = [a[0] + t * dx, a[1] + t * dy];
    return {
      distance: Math.hypot(point[0] - projected[0], point[1] - projected[1]),
      point: projected,
      t,
    };
  }

  function rectPoints(a, b) {
    const left = Math.min(a[0], b[0]);
    const right = Math.max(a[0], b[0]);
    const top = Math.min(a[1], b[1]);
    const bottom = Math.max(a[1], b[1]);
    return [
      [left, top],
      [right, top],
      [right, bottom],
      [left, bottom],
    ];
  }

  return {
    clamp,
    snapPoint,
    polygonArea,
    polygonSelfIntersects,
    distanceToSegment,
    rectPoints,
    samePoint,
  };
});
