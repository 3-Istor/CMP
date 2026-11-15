/**
 * Drawing of the orthogonal edge routes ELK computes. Pure functions, tested
 * in tests/topology.test.mjs.
 */

export interface Point {
  x: number;
  y: number;
}

/** An SVG path through the points, each bend rounded by at most `radius`. */
export function roundedPath(points: Point[], radius = 8): string {
  if (points.length === 0) return "";
  let path = `M${points[0].x},${points[0].y}`;
  for (let i = 1; i < points.length - 1; i++) {
    const previous = points[i - 1];
    const corner = points[i];
    const next = points[i + 1];
    // Half of the shorter segment, so two close bends never overlap.
    const r = Math.min(
      radius,
      Math.hypot(corner.x - previous.x, corner.y - previous.y) / 2,
      Math.hypot(next.x - corner.x, next.y - corner.y) / 2,
    );
    const enter = {
      x: corner.x - Math.sign(corner.x - previous.x) * r,
      y: corner.y - Math.sign(corner.y - previous.y) * r,
    };
    const leave = {
      x: corner.x + Math.sign(next.x - corner.x) * r,
      y: corner.y + Math.sign(next.y - corner.y) * r,
    };
    path += ` L${enter.x},${enter.y} Q${corner.x},${corner.y} ${leave.x},${leave.y}`;
  }
  const last = points[points.length - 1];
  return path + ` L${last.x},${last.y}`;
}

/** The middle of the longest segment: the place where a label covers the least. */
export function routeLabelPoint(points: Point[]): Point {
  let best = { from: points[0], to: points[points.length - 1], length: -1 };
  for (let i = 1; i < points.length; i++) {
    const from = points[i - 1];
    const to = points[i];
    const length = Math.hypot(to.x - from.x, to.y - from.y);
    if (length > best.length) best = { from, to, length };
  }
  return { x: (best.from.x + best.to.x) / 2, y: (best.from.y + best.to.y) / 2 };
}
