import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const G = require("../app/static/zone-geometry.js");

test("polygonArea computes the absolute shoelace area", () => {
  const square = [
    [0, 0],
    [0.5, 0],
    [0.5, 0.5],
    [0, 0.5],
  ];
  assert.equal(G.polygonArea(square), 0.25);
  assert.equal(G.polygonArea(square.slice().reverse()), 0.25);
  assert.equal(G.polygonArea([]), 0);
  assert.equal(G.polygonArea([[0, 0], [1, 1]]), 0);
});

test("polygonSelfIntersects detects a bow tie", () => {
  const bowtie = [
    [0, 0],
    [1, 1],
    [1, 0],
    [0, 1],
  ];
  assert.equal(G.polygonSelfIntersects(bowtie), true);
});

test("polygonSelfIntersects accepts convex and concave shapes", () => {
  assert.equal(
    G.polygonSelfIntersects([
      [0, 0],
      [1, 0],
      [1, 1],
      [0, 1],
    ]),
    false
  );
  assert.equal(
    G.polygonSelfIntersects([
      [0, 0],
      [1, 0],
      [0.5, 0.5],
      [1, 1],
      [0, 1],
    ]),
    false
  );
  assert.equal(G.polygonSelfIntersects([[0, 0], [1, 0], [0, 1]]), false);
});

test("snapPoint rounds to the grid and clamps to the frame", () => {
  assert.deepEqual(G.snapPoint([0.012, 0.019], 0.01), [0.01, 0.02]);
  assert.deepEqual(G.snapPoint([-1, 2], 0.01), [0, 1]);
});

test("distanceToSegment projects onto the segment", () => {
  const hit = G.distanceToSegment([0.5, 0.2], [0, 0], [1, 0]);
  assert.ok(Math.abs(hit.distance - 0.2) < 1e-9);
  assert.deepEqual(hit.point, [0.5, 0]);
  assert.equal(hit.t, 0.5);
  const end = G.distanceToSegment([2, 0], [0, 0], [1, 0]);
  assert.deepEqual(end.point, [1, 0]);
});

test("rectPoints normalizes drag direction", () => {
  assert.deepEqual(G.rectPoints([0.8, 0.7], [0.2, 0.1]), [
    [0.2, 0.1],
    [0.8, 0.1],
    [0.8, 0.7],
    [0.2, 0.7],
  ]);
});
