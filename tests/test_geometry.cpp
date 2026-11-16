#include <cassert>
#include <cmath>
#include <stdexcept>
#include <vector>

#include "../bridge/src/geometry.h"

namespace {
bool close(double lhs, double rhs) {
    return std::abs(lhs - rhs) < 1e-9;
}
}

int main() {
    // The short course-change objective is 200 m along the relative heading.
    Action short_turn;
    short_turn.type = "alter_course";
    short_turn.degrees = 30.0;
    const auto short_points = maneuver_waypoints(10.0, 20.0, 0.0, short_turn);
    assert(short_points.size() == 1);
    assert(close(short_points[0].x, 110.0));
    assert(close(short_points[0].y, 20.0 + 200.0 * std::cos(M_PI / 6.0)));

    const std::vector<Point> route{
        {-100.0, 0.0},
        {0.0, 100.0},
        {0.0, 200.0},
    };

    const auto updated = route_with_intermediate_course_change(
        0.0, 0.0, 0.0, route, 1, 30.0, 40.0
    );

    // The detour is exactly 40 m at 30 degrees from current heading.
    assert(updated.size() == 3);
    assert(close(updated[0].x, 20.0));
    assert(close(updated[0].y, 40.0 * std::cos(M_PI / 6.0)));

    // Completed points are removed, while the next and subsequent mission
    // waypoints are preserved exactly.
    assert(close(updated[1].x, 0.0));
    assert(close(updated[1].y, 100.0));
    assert(close(updated[2].x, 0.0));
    assert(close(updated[2].y, 200.0));

    // Route resumption is derived from physical progress, not a potentially
    // stale MOOS waypoint index. The vessel is on the second leg, so waypoint
    // 0 is behind it and waypoint 2 is the next smooth continuation.
    assert(active_route_waypoint_index(0.0, 150.0, route) == 2);
    // Before reaching the route, retain its first waypoint.
    assert(active_route_waypoint_index(-200.0, -100.0, route) == 0);

    const std::vector<Point> open_waters_route{
        {-665.0, -7550.0},
        {-850.0, -6957.0},
        {-226.0, -5429.0},
        {258.0, -2637.0},
        {12.0, -2084.0},
        {-415.0, -2049.0},
        {-442.0, -2107.0},
    };
    // Regression for the PLEASURE_1 run: the old code resumed at waypoint 0
    // and reversed south. Position-based progress continues north to index 3.
    assert(active_route_waypoint_index(
        192.52326505430662, -3628.8978303922236, open_waters_route
    ) == 3);

    bool rejected_invalid_index = false;
    try {
        (void)route_with_intermediate_course_change(0.0, 0.0, 0.0, route, 3, 5.0, 20.0);
    } catch (const std::runtime_error&) {
        rejected_invalid_index = true;
    }
    assert(rejected_invalid_index);

    bool rejected_short_leg = false;
    try {
        (void)route_with_intermediate_course_change(
            0.0, 0.0, 0.0, route, 1, 5.0, 60.0
        );
    } catch (const std::runtime_error&) {
        rejected_short_leg = true;
    }
    assert(rejected_short_leg);

    Action starboard;
    starboard.type = "turn_starboard";
    starboard.degrees = -25.0;
    assert(migrate_deprecated_turn(starboard));
    assert(starboard.type == "alter_course");
    assert(close(starboard.degrees, 25.0));

    Action port;
    port.type = "turn_port";
    port.degrees = 25.0;
    assert(migrate_deprecated_turn(port));
    assert(port.type == "alter_course");
    assert(close(port.degrees, -25.0));

    Action default_turn;
    default_turn.type = "turn_starboard";
    assert(migrate_deprecated_turn(default_turn));
    assert(close(default_turn.degrees, 30.0));

    // A slower contact behind us and opening is past and clear.
    assert(is_past_and_clear(0.0, 150.0, 0.0, 5.0,
                             0.0, 0.0, 0.0, 2.5));
    // Ahead is not clear, even at adequate range.
    assert(!is_past_and_clear(0.0, 0.0, 0.0, 5.0,
                              0.0, 150.0, 0.0, 2.5));
    // A faster contact behind is closing rather than opening.
    assert(!is_past_and_clear(0.0, 150.0, 0.0, 2.5,
                              0.0, 0.0, 0.0, 5.0));
    // An opening contact remains too close to release the maneuver.
    assert(!is_past_and_clear(0.0, 90.0, 0.0, 5.0,
                              0.0, 0.0, 0.0, 2.5));

    // Returning diagonally toward a route can be unsafe even after the
    // contact is behind on the avoidance heading.
    const double unsafe_rejoin = projected_cpa_distance(
        175.0, 154.0, 333.0, 4.36, 0.0, 154.0, 0.0, 2.5
    );
    assert(unsafe_rejoin < 150.0);
    const double safe_rejoin = projected_cpa_distance(
        300.0, 500.0, 315.0, 4.36, 0.0, 100.0, 0.0, 2.5
    );
    assert(safe_rejoin >= 150.0);
}
