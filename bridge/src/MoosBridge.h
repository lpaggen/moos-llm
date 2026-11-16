#pragma once

#include "MOOS/libMOOS/Comms/MOOSCommClient.h"
#include "geometry.h"

#include <nlohmann/json.hpp>

#include <atomic>
#include <map>
#include <limits>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <thread>
#include <vector>

struct OwnshipState {
    std::string vessel_type;
    double x = 0.0;
    double y = 0.0;
    double heading = 0.0;
    double speed = 0.0;
    double moos_time = -1.0;

    bool have_x = false;
    bool have_y = false;
    bool have_heading = false;
    bool have_speed = false;
    bool have_moos_time = false;

    bool ready() const {
        return have_x && have_y && have_heading && have_speed && have_moos_time;
    }
};

struct CurrentAction {
    Action action;
    std::vector<Point> waypoints;
    double speed = 2.0;
    double completion_radius = 20.0;
};

struct ContactState {
    std::string name;
    std::string vessel_type;
    double x = 0.0;
    double y = 0.0;
    double heading = 0.0;
    double speed = 0.0;
    double range = 0.0;
    double age = 0.0;
    double moos_time = -1.0;
    double position_moos_time = -1.0;
    std::string raw_report;
    std::string raw_contact_info;
    std::string raw_recap;
    std::string colregs_mode;
    std::string colregs_primary_mode;
    std::string colregs_summary;
    double colregs_relevance = 0.0;
    double colregs_priority = 0.0;
    double colregs_mode_index = 0.0;
    double colregs_moos_time = -1.0;

    bool have_x = false;
    bool have_y = false;
    bool have_heading = false;
    bool have_speed = false;
    bool have_range = false;
    bool have_age = false;
    bool have_colregs_relevance = false;
    bool have_colregs_priority = false;
    bool have_colregs_mode_index = false;
};

struct AvoidanceState {
    std::string contact_name;
    std::vector<Point> resume_route;
    int clear_samples = 0;
};

class MoosBridge {
public:
    MoosBridge(
        std::string name,
        std::string moos_host,
        int moos_port,
        int tcp_port,
        std::vector<int> fleet_ports = {}
    );

    bool start();
    void run();

private:
    static bool on_connect_thunk(void* self);
    static bool on_mail_thunk(void* self);
    bool connect_moos();
    void tcp_server_loop();
    void handle_json_line(const std::string& line);
    void send_state_json();
    void send_own_ship_json();
    bool send_json_line(const nlohmann::json& j);

    void dispatch_action(const Action& action);
    void execute_maneuver(const Action& action);
    void publish_waypoints(const std::vector<Point>& waypoints, double speed, double completion_radius);
    void alter_next_route_waypoint(const OwnshipState& state, const Action& action);
    void restore_mission_route();
    double outgoing_moos_time() const;
    bool notify(const std::string& var, const std::string& val);
    bool notify(const std::string& var, double val);
    // void update_maneuver();
    void finish_maneuver();

    double distance_to_waypoint(const Point& p) const;

private:
    std::string name_;
    std::string moos_host_;
    int moos_port_;
    int tcp_port_;
    std::vector<int> fleet_ports_;

    CMOOSCommClient comms_;
    std::vector<std::unique_ptr<CMOOSCommClient>> fleet_comms_;

    mutable std::mutex state_mutex_;
    OwnshipState state_;
    double target_speed_ = 0.0;
    double recovery_speed_ = 0.0;
    std::optional<Point> mission_objective_;
    std::optional<Point> next_waypoint_;
    std::optional<Point> temporary_waypoint_;
    std::vector<Point> mission_route_;
    int mission_waypoint_index_ = 0;
    std::string ownship_name_;
    std::string closest_contact_;
    std::string minimum_range_contact_;
    double minimum_observed_range_ = std::numeric_limits<double>::infinity();
    std::map<std::string, ContactState> contacts_;
    std::optional<CurrentAction> current_action_;
    std::optional<AvoidanceState> avoidance_;
    bool paused_ = false;
    bool mission_complete_ = false;

    std::atomic<bool> running_{true};

    int client_fd_ = -1;
    std::mutex socket_mutex_;
};
