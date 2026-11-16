#pragma once

#include <algorithm>
#include <cmath>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>

struct Point {
    double x;
    double y;
};

struct Action {
    std::string action_class;
    std::string type;
    std::string contact;

    double degrees = 0.0;
    double distance_m = 200.0;
    double original_heading = 0.0;

    double x = 0.0;
    double y = 0.0;

    double speed = -1.0;
    double completion_radius = 20.0;
    std::vector<Point> waypoints;
};

inline double normalize_heading(double deg) {
    double r = std::fmod(deg, 360.0);
    if (r < 0.0) r += 360.0;
    return r;
}

inline Point project_point(double x, double y, double heading_deg, double distance_m) {
    double rad = heading_deg * M_PI / 180.0;
    return {
        x + distance_m * std::sin(rad),
        y + distance_m * std::cos(rad)
    };
}

inline bool is_past_and_clear(
    double own_x,
    double own_y,
    double own_heading,
    double own_speed,
    double contact_x,
    double contact_y,
    double contact_heading,
    double contact_speed,
    double minimum_range_m = 100.0
) {
    const double dx = contact_x - own_x;
    const double dy = contact_y - own_y;
    const double range = std::hypot(dx, dy);
    if (range < minimum_range_m || range <= 1e-6) return false;
    const double bearing = normalize_heading(std::atan2(dx, dy) * 180.0 / M_PI);
    const double relative_bearing = normalize_heading(bearing - own_heading);
    const bool abaft_beam = relative_bearing > 112.5 && relative_bearing < 247.5;
    const double own_rad = own_heading * M_PI / 180.0;
    const double contact_rad = contact_heading * M_PI / 180.0;
    const double relative_vx = contact_speed * std::sin(contact_rad) -
                               own_speed * std::sin(own_rad);
    const double relative_vy = contact_speed * std::cos(contact_rad) -
                               own_speed * std::cos(own_rad);
    const double range_rate = (dx * relative_vx + dy * relative_vy) / range;
    return abaft_beam && range_rate > 0.1;
}

inline double projected_cpa_distance(
    double own_x,
    double own_y,
    double own_heading,
    double own_speed,
    double contact_x,
    double contact_y,
    double contact_heading,
    double contact_speed
) {
    const double dx = contact_x - own_x;
    const double dy = contact_y - own_y;
    const double own_rad = own_heading * M_PI / 180.0;
    const double contact_rad = contact_heading * M_PI / 180.0;
    const double rvx = contact_speed * std::sin(contact_rad) -
                       own_speed * std::sin(own_rad);
    const double rvy = contact_speed * std::cos(contact_rad) -
                       own_speed * std::cos(own_rad);
    const double relative_speed_sq = rvx * rvx + rvy * rvy;
    if (relative_speed_sq <= 1e-9) return std::hypot(dx, dy);
    const double tcpa = -(dx * rvx + dy * rvy) / relative_speed_sq;
    if (tcpa <= 0.0) return std::hypot(dx, dy);
    return std::hypot(dx + rvx * tcpa, dy + rvy * tcpa);
}

inline size_t active_route_waypoint_index(
    double x,
    double y,
    const std::vector<Point>& route
) {
    if (route.empty()) {
        throw std::runtime_error("cannot locate an active waypoint on an empty route");
    }
    if (route.size() == 1) return 0;

    double best_distance_sq = std::numeric_limits<double>::infinity();
    size_t best_next_index = 0;
    for (size_t i = 0; i + 1 < route.size(); ++i) {
        const double segment_x = route[i + 1].x - route[i].x;
        const double segment_y = route[i + 1].y - route[i].y;
        const double length_sq = segment_x * segment_x + segment_y * segment_y;
        if (length_sq <= 1e-9) continue;

        const double raw_fraction =
            ((x - route[i].x) * segment_x + (y - route[i].y) * segment_y) /
            length_sq;
        const double fraction = std::clamp(raw_fraction, 0.0, 1.0);
        const double nearest_x = route[i].x + fraction * segment_x;
        const double nearest_y = route[i].y + fraction * segment_y;
        const double dx = x - nearest_x;
        const double dy = y - nearest_y;
        const double distance_sq = dx * dx + dy * dy;

        // Before the first leg, its first point is still the next waypoint.
        // On or beyond any active leg, continue to that leg's endpoint.
        const size_t next_index = (i == 0 && raw_fraction < 0.0) ? 0 : i + 1;
        // Prefer the later leg at a shared waypoint so a completed point is
        // never selected again.
        if (distance_sq <= best_distance_sq) {
            best_distance_sq = distance_sq;
            best_next_index = next_index;
        }
    }

    return best_next_index;
}

inline std::vector<Point> route_with_intermediate_course_change(
    double x,
    double y,
    double heading_deg,
    const std::vector<Point>& route,
    size_t next_index,
    double degrees,
    double maneuver_distance_m
) {
    if (next_index >= route.size()) {
        throw std::runtime_error("alter_course requires a valid next waypoint");
    }

    const Point& next = route[next_index];
    const double dx = next.x - x;
    const double dy = next.y - y;
    const double distance_to_next = std::hypot(dx, dy);
    if (distance_to_next <= 1e-6) {
        throw std::runtime_error("alter_course next waypoint is at the vessel position");
    }
    if (maneuver_distance_m <= 0.0) {
        throw std::runtime_error("alter_course requires a positive maneuver distance");
    }
    if (distance_to_next < 2.0 * maneuver_distance_m) {
        throw std::runtime_error(
            "alter_course is too close to the next waypoint"
        );
    }

    // `degrees` is a signed change from the vessel's present course and
    // `maneuver_distance_m` is the length of that temporary altered leg.
    // The former implementation used the bearing to the mission waypoint and
    // half of the remaining mission leg, making a nominal 120 m avoidance
    // command produce detours hundreds of metres long.
    const Point intermediate = project_point(
        x,
        y,
        normalize_heading(heading_deg + std::clamp(degrees, -60.0, 60.0)),
        maneuver_distance_m
    );

    // Omit completed waypoints: updating BHV_Waypoint's points list restarts
    // traversal at index zero.  The original next waypoint and every later
    // waypoint remain intact after the temporary course-change point.
    std::vector<Point> updated_route;
    updated_route.reserve(route.size() - next_index + 1);
    updated_route.push_back(intermediate);
    updated_route.insert(
        updated_route.end(),
        route.begin() + static_cast<std::ptrdiff_t>(next_index),
        route.end()
    );
    return updated_route;
}

inline bool migrate_deprecated_turn(Action& action) {
    if (action.type == "turn_starboard") {
        action.type = "alter_course";
        action.degrees = action.degrees == 0.0 ? 30.0 : std::abs(action.degrees);
        return true;
    }
    if (action.type == "turn_port") {
        action.type = "alter_course";
        action.degrees = action.degrees == 0.0 ? -30.0 : -std::abs(action.degrees);
        return true;
    }
    return false;
}

inline std::vector<Point> maneuver_waypoints(
    double x,
    double y,
    double heading,
    const Action& action
) {
    if (action.type == "alter_course") {
        return {project_point(
            x, y, normalize_heading(heading + action.degrees),
            std::max(10.0, action.distance_m)
        )};
    }
    if (action.type == "maintain_course") {
        return {project_point(x, y, heading, action.distance_m)};
    }

    if (action.type == "resume_course") {
        return {project_point(x, y, action.original_heading, action.distance_m)};
    }

    if (action.type == "follow_waypoint") {
        return {{action.x, action.y}};
    }

    if (action.type == "reroute") {
        return action.waypoints;
    }

    throw std::runtime_error("unknown maneuver type: " + action.type);
}
