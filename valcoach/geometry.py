"""Conservative planar geometry and bounded walking routes; world units are cm."""

from __future__ import annotations

from dataclasses import dataclass, field
import heapq
import math

Point = tuple[float, float]
Polygon = tuple[Point, ...]


def distance(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def point_segment_distance(point: Point, a: Point, b: Point) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    scale = dx*dx + dy*dy
    t = max(0.0, min(1.0, ((point[0]-a[0])*dx + (point[1]-a[1])*dy)/scale)) if scale else 0.0
    return distance(point, (a[0]+t*dx, a[1]+t*dy))


def edges(polygon: Polygon):
    return zip(polygon, polygon[1:] + polygon[:1])


def edge_distance(point: Point, polygon: Polygon) -> float:
    return min(point_segment_distance(point, a, b) for a, b in edges(polygon))


def contains(point: Point, polygon: Polygon) -> bool:
    """Interior only; callers handle edge uncertainty separately."""
    x, y = point
    inside = False
    for a, b in edges(polygon):
        if (a[1] > y) != (b[1] > y) and x < (b[0]-a[0])*(y-a[1])/(b[1]-a[1]) + a[0]:
            inside = not inside
    return inside


def _cross(a: Point, b: Point, c: Point) -> float:
    return (b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0])


def crosses(a: Point, b: Point, c: Point, d: Point) -> bool:
    return _cross(a, b, c)*_cross(a, b, d) < 0 and _cross(c, d, a)*_cross(c, d, b) < 0


def segment_distance(a: Point, b: Point, c: Point, d: Point) -> float:
    if crosses(a, b, c, d):
        return 0.0
    return min(point_segment_distance(p, q, r) for p, q, r in ((a,c,d), (b,c,d), (c,a,b), (d,a,b)))


def nearby_edges(a: Point, b: Point, polygon: Polygon, margin: float):
    """Reject distant edges before exact segment tests on full-map outlines."""
    left, right = min(a[0], b[0])-margin, max(a[0], b[0])+margin
    bottom, top = min(a[1], b[1])-margin, max(a[1], b[1])+margin
    return ((c, d) for c, d in edges(polygon)
            if max(c[0], d[0]) >= left and min(c[0], d[0]) <= right
            and max(c[1], d[1]) >= bottom and min(c[1], d[1]) <= top)


def near_boundary(point: Point, polygon: Polygon, margin: float) -> bool:
    return any(point_segment_distance(point, a, b) <= margin for a, b in nearby_edges(point, point, polygon, margin))


def inside_segment(a: Point, b: Point, polygon: Polygon, clearance: float = 0) -> bool:
    if not contains(a, polygon) or not contains(b, polygon):
        return False
    return all(not crosses(a, b, c, d) and segment_distance(a, b, c, d) > clearance
               for c, d in nearby_edges(a, b, polygon, clearance))


def _penetrates(a: Point, b: Point, polygon: Polygon, epsilon: float) -> bool:
    """Require interior evidence beyond the annotation's edge tolerance."""
    parameters = [0.0, 1.0]
    dx, dy = b[0]-a[0], b[1]-a[1]
    for c, d in edges(polygon):
        if crosses(a, b, c, d):
            ex, ey = d[0]-c[0], d[1]-c[1]
            parameters.append(((c[0]-a[0])*ey-(c[1]-a[1])*ex)/(dx*ey-dy*ex))
    parameters.sort()
    for start, end in zip(parameters, parameters[1:]):
        midpoint = (a[0]+dx*(start+end)/2, a[1]+dy*(start+end)/2)
        if contains(midpoint, polygon) and edge_distance(midpoint, polygon) > epsilon:
            return True
    return False


def sightline(a: Point, b: Point, coverage: Polygon, blockers: list[dict], epsilon: float) -> dict:
    if not inside_segment(a, b, coverage, epsilon):
        return {"status": "unknown", "reason": "outside_or_near_coverage_edge", "blocker_ids": []}
    blocked, uncertain = [], []
    for blocker in blockers:
        polygon = blocker["polygon"]
        if contains(a, polygon) or contains(b, polygon):
            return {"status": "unknown", "reason": "sample_inside_sight_blocker", "blocker_ids": [blocker["id"]]}
        if any(crosses(a, b, c, d) for c, d in edges(polygon)):
            (blocked if _penetrates(a, b, polygon, epsilon) else uncertain).append(blocker["id"])
        elif any(segment_distance(a, b, c, d) <= epsilon for c, d in edges(polygon)):
            uncertain.append(blocker["id"])
    if blocked:
        return {"status": "blocked_in_annotated_2d_model", "reason": "footprint_intersection", "blocker_ids": blocked}
    if uncertain:
        return {"status": "unknown", "reason": "near_sight_blocker_edge", "blocker_ids": uncertain}
    return {"status": "clear_in_annotated_2d_model", "reason": "no_annotated_footprint_intersection", "blocker_ids": []}


def _simplify(points: list[Point]) -> list[Point]:
    result = []
    for point in points:
        while len(result) > 1 and abs(_cross(result[-2], result[-1], point)) < 1e-7:
            result.pop()
        if not result or distance(result[-1], point) > 1e-7:
            result.append(point)
    return result


@dataclass
class WalkingGrid:
    """A* on one annotated walking surface, with body-clearance checks."""
    surface: Polygon
    blockers: list[dict]
    resolution: float
    radius: float
    max_cells: int = 20000
    _validity: dict = field(default_factory=dict, init=False)
    _connections: dict = field(default_factory=dict, init=False)

    def __post_init__(self):
        self.origin = (min(p[0] for p in self.surface), min(p[1] for p in self.surface))
        self.width = math.ceil((max(p[0] for p in self.surface)-self.origin[0])/self.resolution)
        self.height = math.ceil((max(p[1] for p in self.surface)-self.origin[1])/self.resolution)
        if self.width*self.height > self.max_cells:
            raise ValueError("Walking surface exceeds the bounded grid size.")

    def center(self, node: tuple[int, int]) -> Point:
        return tuple(self.origin[i] + (node[i]+0.5)*self.resolution for i in (0,1))

    def point_valid(self, point: Point) -> bool:
        return (contains(point, self.surface) and not near_boundary(point, self.surface, self.radius)
                and all(not contains(point, b["polygon"]) and not near_boundary(point, b["polygon"], self.radius)
                        for b in self.blockers))

    def node_valid(self, node: tuple[int, int]) -> bool:
        if node not in self._validity:
            x, y = node
            self._validity[node] = (0 <= x < self.width and 0 <= y < self.height and self.point_valid(self.center(node)))
        return self._validity[node]

    def connection_valid(self, a: Point, b: Point) -> bool:
        return (inside_segment(a, b, self.surface, self.radius)
                and all(not contains(a, item["polygon"]) and not contains(b, item["polygon"])
                        and all(segment_distance(a, b, c, d) > self.radius for c, d in nearby_edges(a,b,item["polygon"],self.radius))
                        for item in self.blockers))

    def nearest_node(self, point: Point):
        base = tuple(math.floor((point[i]-self.origin[i])/self.resolution) for i in (0,1))
        nodes = [(base[0]+dx, base[1]+dy) for dx in (-1,0,1) for dy in (-1,0,1)]
        return next((n for n in sorted(nodes, key=lambda n: distance(point, self.center(n)))
                     if self.node_valid(n) and self.connection_valid(point, self.center(n))), None)

    def _neighbors(self, node):
        for dx, dy in ((1,0),(-1,0),(0,1),(0,-1),(1,1),(1,-1),(-1,1),(-1,-1)):
            other = (node[0]+dx, node[1]+dy)
            if not self.node_valid(other):
                continue
            if dx and dy and not (self.node_valid((node[0]+dx,node[1])) and self.node_valid((node[0],node[1]+dy))):
                continue
            pair = tuple(sorted((node, other)))
            if pair not in self._connections:
                self._connections[pair] = self.connection_valid(self.center(node), self.center(other))
            if self._connections[pair]:
                yield other

    def _search(self, start, goal):
        queue, cost, previous = [(0.0, start)], {start: 0.0}, {}
        visited = set()
        while queue and len(visited) < self.max_cells:
            _, node = heapq.heappop(queue)
            if node in visited:
                continue
            if node == goal:
                path = [node]
                while path[-1] != start:
                    path.append(previous[path[-1]])
                return list(reversed(path)), "found"
            visited.add(node)
            for other in self._neighbors(node):
                value = cost[node] + distance(self.center(node), self.center(other))
                if value < cost.get(other, math.inf):
                    cost[other], previous[other] = value, node
                    heapq.heappush(queue, (value+distance(self.center(other),self.center(goal)), other))
        return None, "no_route_in_modeled_area" if not queue else "search_budget_exhausted"

    def route(self, a: Point, b: Point) -> dict:
        if not self.point_valid(a) or not self.point_valid(b):
            return {"status": "unknown", "reason": "endpoint_outside_walking_space_or_clearance"}
        if self.connection_valid(a, b):
            points = [a,b]
        else:
            start, goal = self.nearest_node(a), self.nearest_node(b)
            if start is None or goal is None:
                return {"status": "unknown", "reason": "endpoint_not_connected_to_grid"}
            nodes, reason = self._search(start, goal)
            if nodes is None:
                return {"status": "no_route_in_modeled_area" if reason == "no_route_in_modeled_area" else "unknown", "reason": reason}
            points = _simplify([a, *(self.center(n) for n in nodes), b])
        length = sum(distance(x,y) for x,y in zip(points,points[1:]))/100
        return {"status": "walking_route_found_in_model", "length_m": round(length,3),
                "vertices_cm": [list(p) for p in points[:64]], "total_vertices": len(points),
                "vertices_truncated": len(points)>64, "grid_resolution_cm": self.resolution,
                "actor_clearance_radius_cm": self.radius,
                "method": "annotated_2d_surface; straight_segment_or_bounded_grid_A_star"}
