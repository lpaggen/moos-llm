#include "MoosBridge.h"

#include <nlohmann/json.hpp>

#include "MOOS/libMOOS/Utils/MOOSUtilityFunctions.h"

#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>

#include <algorithm>
#include <cctype>
#include <chrono>
#include <cmath>
#include <cstring>
#include <iostream>
#include <map>
#include <sstream>

using json = nlohmann::json;

namespace {

std::string trim(std::string s) {
    auto not_space = [](unsigned char c) { return !std::isspace(c); };
    s.erase(s.begin(), std::find_if(s.begin(), s.end(), not_space));
    s.erase(std::find_if(s.rbegin(), s.rend(), not_space).base(), s.end());
    return s;
}

std::string upper(std::string s) {
    std::transform(s.begin(), s.end(), s.begin(), [](unsigned char c) {
        return static_cast<char>(std::toupper(c));
    });
    return s;
}

std::map<std::string, std::string> parse_key_values(const std::string& report) {
    std::map<std::string, std::string> fields;
    std::string normalized = report;
    std::replace(normalized.begin(), normalized.end(), '#', ',');

    std::stringstream ss(normalized);
    std::string item;

    while (std::getline(ss, item, ',')) {
        const size_t eq = item.find('=');
        if (eq == std::string::npos) continue;

        std::string key = upper(trim(item.substr(0, eq)));
        std::string val = trim(item.substr(eq + 1));
        if (!key.empty()) fields[key] = val;
    }

    return fields;
}

bool get_double_field(
    const std::map<std::string, std::string>& fields,
    const std::string& key,
    double& out
) {
    auto it = fields.find(key);
    if (it == fields.end()) return false;

    try {
        out = std::stod(it->second);
        return true;
    } catch (const std::exception&) {
        return false;
    }
}

std::string get_string_field(
    const std::map<std::string, std::string>& fields,
    const std::string& key
) {
    auto it = fields.find(key);
    if (it == fields.end()) return "";
    return it->second;
}

std::vector<std::string> split_names(const std::string& names) {
    std::vector<std::string> result;
    std::stringstream ss(names);
    std::string item;

    while (std::getline(ss, item, ',')) {
        item = trim(item);
        if (!item.empty()) result.push_back(item);
    }

    return result;
}

std::vector<Point> parse_waypoints(const json& j) {
    std::vector<Point> waypoints;
    if (!j.is_array()) return waypoints;

    for (const auto& item : j) {
        if (!item.is_object()) continue;
        if (!item.contains("x") || !item.contains("y")) continue;
        waypoints.push_back({item.value("x", 0.0), item.value("y", 0.0)});
    }
    return waypoints;
}

json contact_to_json(const OwnshipState& ownship, const ContactState& contact) {
    json j = {
        {"name", contact.name},
        {"vessel_type", contact.vessel_type.empty() ? json(nullptr) : json(contact.vessel_type)},
        {"ready", (contact.have_x && contact.have_y && contact.have_heading && contact.have_speed) ||
                  contact.have_range || contact.have_age},
        {"moos_time", contact.moos_time},
        {"nav", {
            {"x", contact.x},
            {"y", contact.y},
            {"heading", contact.heading},
            {"speed", contact.speed}
        }},
        {"range", contact.have_range ? json(contact.range) : json(nullptr)},
        {"age", contact.have_age ? json(contact.age) : json(nullptr)},
        {"raw_report", contact.raw_report},
        {"raw_contact_info", contact.raw_contact_info},
        {"raw_recap", contact.raw_recap},
        {"moos_colregs", {
            {"mode", contact.colregs_mode.empty() ? json(nullptr) : json(contact.colregs_mode)},
            {"primary_mode", contact.colregs_primary_mode.empty() ? json(nullptr) : json(contact.colregs_primary_mode)},
            {"summary", contact.colregs_summary.empty() ? json(nullptr) : json(contact.colregs_summary)},
            {"relevance", contact.have_colregs_relevance ? json(contact.colregs_relevance) : json(nullptr)},
            {"priority", contact.have_colregs_priority ? json(contact.colregs_priority) : json(nullptr)},
            {"mode_index", contact.have_colregs_mode_index ? json(contact.colregs_mode_index) : json(nullptr)},
            {"moos_time", contact.colregs_moos_time >= 0.0 ? json(contact.colregs_moos_time) : json(nullptr)}
        }}
    };

    if (ownship.have_moos_time && contact.moos_time >= 0.0) {
        j["age_s"] = ownship.moos_time - contact.moos_time;
    }

    return j;
}

}  // namespace

MoosBridge::MoosBridge(
    std::string name,
    std::string moos_host,
    int moos_port,
    int tcp_port,
    std::vector<int> fleet_ports
)
    : name_(std::move(name)),
      moos_host_(std::move(moos_host)),
      moos_port_(moos_port),
      tcp_port_(tcp_port),
      fleet_ports_(std::move(fleet_ports)) {}

bool MoosBridge::start() {
    if (!connect_moos()) {
        std::cerr << "failed to connect to MOOS\n";
        return false;
    }

    // Maintain one persistent MOOS connection per additional vessel.  A
    // lockstep pause must be posted directly to every simulator: routing it
    // through the field brokers is not atomic and, at high warp, even a small
    // broker delay allows substantial motion during model inference.
    for (const int port : fleet_ports_) {
        if (port == moos_port_) continue;
        auto client = std::make_unique<CMOOSCommClient>();
        const std::string client_name = name_ + "_fleet_" + std::to_string(port);
        client->Run(moos_host_.c_str(), port, client_name.c_str());
        for (int attempt = 0; attempt < 50 && !client->IsConnected(); ++attempt) {
            std::this_thread::sleep_for(std::chrono::milliseconds(20));
        }
        if (!client->IsConnected()) {
            std::cerr << "failed to connect fleet pause client to MOOS port "
                      << port << "\n";
            return false;
        }
        fleet_comms_.push_back(std::move(client));
    }

    std::thread(&MoosBridge::tcp_server_loop, this).detach();
    return true;
}

bool MoosBridge::on_connect_thunk(void* self) {
    auto* bridge = static_cast<MoosBridge*>(self);

    bridge->comms_.Register("NAV_X", 0);
    bridge->comms_.Register("NAV_Y", 0);
    bridge->comms_.Register("NAV_HEADING", 0);
    bridge->comms_.Register("NAV_SPEED", 0);
    bridge->comms_.Register("DESIRED_SPEED", 0);
    bridge->comms_.Register("NODE_REPORT", 0);
    bridge->comms_.Register("NODE_REPORT_LOCAL", 0);
    bridge->comms_.Register("CONTACT_INFO", 0);
    bridge->comms_.Register("CONTACT_INFO_ALL", 0);
    bridge->comms_.Register("CONTACTS_LIST", 0);
    bridge->comms_.Register("CONTACTS_RECAP", 0);
    bridge->comms_.Register("CONTACT_CLOSEST", 0);
    bridge->comms_.Register("CONTACT_CLOSEST_RANGE", 0);
    bridge->comms_.Register("LLM_STOP", 0);
    bridge->comms_.Register("LLM_MANEUVER_DONE", 0);
    bridge->comms_.Register("MISSION_OBJECTIVE", 0);
    bridge->comms_.Register("MISSION_ROUTE", 0);
    bridge->comms_.Register("MISSION_COMPLETE", 0);
    bridge->comms_.Register("WPT_INDEX_CROSSING", 0);
    bridge->comms_.Register("WPT_STAT_CROSSING", 0);
    // BHV_AvdColregsV22 publishes these variables once per helm iteration and
    // suffixes each one with the uppercase contact name.
    bridge->comms_.Register("COLREGS_AVOID_MODE_*", "*", 0);
    bridge->comms_.Register("COLREGS_SUMMARY_*", "*", 0);
    bridge->comms_.Register("COLREGS_AVOID_IX_*", "*", 0);
    bridge->comms_.Register("COLREGS_AVOID_PWT_*", "*", 0);
    bridge->comms_.Register("COL22_AVOID_MODE_*", "*", 0);
    bridge->comms_.Register("COL22_RELEVANCE_*", "*", 0);
    bridge->comms_.Register("AVDCOL_RANGE_*", "*", 0);

    return true;
}

bool MoosBridge::on_mail_thunk(void* self) {
    auto* bridge = static_cast<MoosBridge*>(self);
    MOOSMSG_LIST mail;
    bridge->comms_.Fetch(mail);

    std::unique_lock<std::mutex> lock(bridge->state_mutex_);

    for (auto& msg : mail) {
        const std::string key = msg.GetKey();
        const double msg_time = msg.GetTime();
        if (msg_time > bridge->state_.moos_time) {
            bridge->state_.moos_time = msg_time;
            bridge->state_.have_moos_time = true;
        }

        auto colregs_contact = [&](const std::string& prefix) -> ContactState* {
            if (key.rfind(prefix, 0) != 0) return nullptr;
            const std::string suffix = key.substr(prefix.size());
            for (auto& [name, contact] : bridge->contacts_) {
                if (upper(name) == suffix) return &contact;
            }
            ContactState& contact = bridge->contacts_[suffix];
            contact.name = suffix;
            return &contact;
        };

        if (ContactState* contact = colregs_contact("COLREGS_AVOID_MODE_")) {
            contact->colregs_mode = msg.GetString();
            contact->colregs_moos_time = msg_time;
        } else if (ContactState* contact = colregs_contact("COLREGS_SUMMARY_")) {
            contact->colregs_summary = msg.GetString();
            contact->colregs_moos_time = msg_time;
        } else if (ContactState* contact = colregs_contact("COLREGS_AVOID_IX_")) {
            contact->colregs_mode_index = msg.GetDouble();
            contact->have_colregs_mode_index = true;
            contact->colregs_moos_time = msg_time;
        } else if (ContactState* contact = colregs_contact("COLREGS_AVOID_PWT_")) {
            contact->colregs_priority = msg.GetDouble();
            contact->have_colregs_priority = true;
            contact->colregs_moos_time = msg_time;
        } else if (ContactState* contact = colregs_contact("COL22_AVOID_MODE_")) {
            contact->colregs_primary_mode = msg.GetString();
            contact->colregs_moos_time = msg_time;
        } else if (ContactState* contact = colregs_contact("COL22_RELEVANCE_")) {
            contact->colregs_relevance = msg.GetDouble();
            contact->have_colregs_relevance = true;
            contact->colregs_moos_time = msg_time;
        } else if (ContactState* contact = colregs_contact("AVDCOL_RANGE_")) {
            contact->range = msg.GetDouble();
            contact->have_range = true;
            contact->colregs_moos_time = msg_time;
        } else if (key == "NAV_X") {
            bridge->state_.x = msg.GetDouble();
            bridge->state_.have_x = true;
        } else if (key == "NAV_Y") {
            bridge->state_.y = msg.GetDouble();
            bridge->state_.have_y = true;
        } else if (key == "NAV_HEADING") {
            bridge->state_.heading = msg.GetDouble();
            bridge->state_.have_heading = true;
        } else if (key == "NAV_SPEED") {
            bridge->state_.speed = msg.GetDouble();
            bridge->state_.have_speed = true;
        } else if (key == "DESIRED_SPEED") {
            bridge->target_speed_ = msg.GetDouble();
            if (bridge->target_speed_ > bridge->recovery_speed_) {
                bridge->recovery_speed_ = bridge->target_speed_;
            }
        } else if (key == "MISSION_OBJECTIVE") {
            const auto fields = parse_key_values(msg.GetString());
            Point objective;
            if (get_double_field(fields, "X", objective.x) &&
                get_double_field(fields, "Y", objective.y)) {
                bridge->next_waypoint_ = objective;
                if (bridge->temporary_waypoint_ &&
                    std::hypot(
                        bridge->temporary_waypoint_->x - objective.x,
                        bridge->temporary_waypoint_->y - objective.y
                    ) > 1.0) {
                    bridge->temporary_waypoint_.reset();
                }
            }
        } else if (key == "MISSION_ROUTE") {
            std::string route = msg.GetString();
            const std::string prefix = "points=";
            if (route.rfind(prefix, 0) == 0) route.erase(0, prefix.size());
            std::vector<Point> parsed;
            std::stringstream points(route);
            std::string item;
            while (std::getline(points, item, ':')) {
                const size_t comma = item.find(',');
                if (comma == std::string::npos) continue;
                try {
                    parsed.push_back({
                        std::stod(item.substr(0, comma)),
                        std::stod(item.substr(comma + 1))
                    });
                } catch (const std::exception&) {
                    parsed.clear();
                    break;
                }
            }
            if (!parsed.empty()) {
                // The route's last point is the mission destination.  Capture
                // it once; live waypoint updates must not redefine the goal.
                if (!bridge->mission_objective_) {
                    bridge->mission_objective_ = parsed.back();
                }
                bridge->mission_route_ = std::move(parsed);
            }
        } else if (key == "MISSION_COMPLETE") {
            std::string value = msg.GetString();
            std::transform(value.begin(), value.end(), value.begin(), ::tolower);
            // Mission completion is terminal for this bridge process. An LLM
            // decision may have been requested just before the helm completed
            // the route and arrive just after its TRUE endflag. Never allow a
            // later/stale FALSE publication to erase that terminal outcome.
            if (value == "true" || value == "1" || value == "yes") {
                bridge->mission_complete_ = true;
            }
        } else if (key == "WPT_INDEX_CROSSING") {
            bridge->mission_waypoint_index_ = static_cast<int>(msg.GetDouble());
        } else if (key == "WPT_STAT_CROSSING") {
            const auto fields = parse_key_values(msg.GetString());
            double index = 0.0;
            if (get_double_field(fields, "INDEX", index)) {
                bridge->mission_waypoint_index_ = static_cast<int>(index);
            }
        } else if (key == "NODE_REPORT" || key == "NODE_REPORT_LOCAL") {
            const std::string raw = msg.GetString();
            auto fields = parse_key_values(raw);
            std::string contact_name = get_string_field(fields, "NAME");
            if (contact_name.empty()) contact_name = get_string_field(fields, "VNAME");

            if (!contact_name.empty()) {
                if (key == "NODE_REPORT_LOCAL") {
                    bridge->ownship_name_ = contact_name;
                    bridge->state_.vessel_type = get_string_field(fields, "TYPE");
                } else if (contact_name != bridge->ownship_name_) {
                    ContactState& contact = bridge->contacts_[contact_name];
                    contact.name = contact_name;
                    contact.vessel_type = get_string_field(fields, "TYPE");
                    contact.moos_time = msg_time;
                    contact.position_moos_time = msg_time;
                    contact.raw_report = raw;

                    double value = 0.0;
                    if (get_double_field(fields, "X", value)) {
                        contact.x = value;
                        contact.have_x = true;
                    }
                    if (get_double_field(fields, "Y", value)) {
                        contact.y = value;
                        contact.have_y = true;
                    }
                    if (get_double_field(fields, "HDG", value) ||
                        get_double_field(fields, "HEADING", value)) {
                        contact.heading = value;
                        contact.have_heading = true;
                    }
                    if (get_double_field(fields, "SPD", value) ||
                        get_double_field(fields, "SPEED", value)) {
                        contact.speed = value;
                        contact.have_speed = true;
                    }
                }
            }
        } else if (key == "CONTACT_INFO" || key == "CONTACT_INFO_ALL") {
            const std::string raw = msg.GetString();
            auto fields = parse_key_values(raw);
            std::string contact_name = get_string_field(fields, "CONTACT");
            if (contact_name.empty()) contact_name = get_string_field(fields, "NAME");
            if (contact_name.empty()) contact_name = get_string_field(fields, "VNAME");

            if (!contact_name.empty() && contact_name != bridge->ownship_name_) {
                ContactState& contact = bridge->contacts_[contact_name];
                contact.name = contact_name;
                contact.moos_time = msg_time;
                contact.raw_contact_info = raw;
            }
        } else if (key == "CONTACTS_LIST") {
            const std::string raw = msg.GetString();
            for (const std::string& contact_name : split_names(raw)) {
                if (contact_name == bridge->ownship_name_) continue;

                ContactState& contact = bridge->contacts_[contact_name];
                contact.name = contact_name;
                contact.moos_time = msg_time;
            }
        } else if (key == "CONTACTS_RECAP") {
            const std::string raw = msg.GetString();
            std::stringstream ss(raw);
            std::string item;

            while (std::getline(ss, item, '#')) {
                item = trim(item);
                if (item.empty()) continue;

                auto fields = parse_key_values(item);
                std::string contact_name = get_string_field(fields, "VNAME");
                if (contact_name.empty()) contact_name = get_string_field(fields, "NAME");
                if (contact_name.empty() || contact_name == bridge->ownship_name_) continue;

                ContactState& contact = bridge->contacts_[contact_name];
                contact.name = contact_name;
                contact.moos_time = msg_time;
                contact.raw_recap = item;

                double value = 0.0;
                if (get_double_field(fields, "RANGE", value)) {
                    contact.range = value;
                    contact.have_range = true;
                }
                if (get_double_field(fields, "AGE", value)) {
                    contact.age = value;
                    contact.have_age = true;
                }
            }
        } else if (key == "CONTACT_CLOSEST") {
            const std::string contact_name = trim(msg.GetString());
            if (!contact_name.empty() && contact_name != bridge->ownship_name_) {
                bridge->closest_contact_ = contact_name;
                ContactState& contact = bridge->contacts_[contact_name];
                contact.name = contact_name;
                contact.moos_time = msg_time;
            }
        } else if (key == "CONTACT_CLOSEST_RANGE") {
            if (!bridge->closest_contact_.empty()) {
                ContactState& contact = bridge->contacts_[bridge->closest_contact_];
                contact.name = bridge->closest_contact_;
                contact.range = msg.GetDouble();
                contact.have_range = true;
                contact.moos_time = msg_time;
            }
        } else if (key == "LLM_STOP") {
            bridge->paused_ = upper(trim(msg.GetString())) == "TRUE";
        } else if (key == "LLM_MANEUVER_DONE" && msg.GetString() == "true") {
            bridge->comms_.Notify("LLM_MANEUVER_DONE", "false", msg_time);
            if (bridge->avoidance_) {
                // Release the completed short waypoint and resume the mission.
                // restore_mission_route takes the state lock itself.
                lock.unlock();
                bridge->restore_mission_route();
                lock.lock();
            } else {
                bridge->current_action_.reset();
                bridge->temporary_waypoint_.reset();
                bridge->comms_.Notify("LLM_CONTROL", "false", msg_time);
                std::cout << "maneuver completed by helm\n";
            }
        }
    }

    // Latch actual closest separation from high-rate navigation reports. This
    // is an observed outcome, distinct from a future constant-velocity CPA.
    if (bridge->state_.have_x && bridge->state_.have_y) {
        for (const auto& entry : bridge->contacts_) {
            const ContactState& contact = entry.second;
            if (!contact.have_x || !contact.have_y) continue;
            if (contact.position_moos_time < 0.0 ||
                bridge->state_.moos_time - contact.position_moos_time > 5.0) continue;
            const double range = std::hypot(
                contact.x - bridge->state_.x,
                contact.y - bridge->state_.y
            );
            if (range < bridge->minimum_observed_range_) {
                bridge->minimum_observed_range_ = range;
                bridge->minimum_range_contact_ = contact.name;
            }
        }
    }
    return true;
}

bool MoosBridge::connect_moos() {
    comms_.SetOnConnectCallBack(&MoosBridge::on_connect_thunk, this);
    comms_.SetOnMailCallBack(&MoosBridge::on_mail_thunk, this);

    comms_.Run(moos_host_.c_str(), moos_port_, name_.c_str());
    std::this_thread::sleep_for(std::chrono::milliseconds(800));
    return true;
}

void MoosBridge::run() {
    while (running_) {
        send_state_json();
        std::this_thread::sleep_for(std::chrono::milliseconds(500));
    }
}

void MoosBridge::tcp_server_loop() {
    int server_fd = socket(AF_INET, SOCK_STREAM, 0);
    if (server_fd < 0) {
        std::cerr << "socket() failed\n";
        return;
    }

    int opt = 1;
    setsockopt(server_fd, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));

    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = inet_addr("127.0.0.1");
    addr.sin_port = htons(tcp_port_);

    if (bind(server_fd, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) < 0) {
        std::cerr << "bind() failed on 127.0.0.1:" << tcp_port_ << "\n";
        close(server_fd);
        return;
    }

    if (listen(server_fd, 1) < 0) {
        std::cerr << "listen() failed\n";
        close(server_fd);
        return;
    }

    std::cout << "TCP bridge listening on 127.0.0.1:" << tcp_port_ << "\n";

    while (running_) {
        sockaddr_in client_addr{};
        socklen_t client_len = sizeof(client_addr);

        int fd = accept(server_fd, reinterpret_cast<sockaddr*>(&client_addr), &client_len);
        if (fd < 0) continue;

        {
            std::lock_guard<std::mutex> lock(socket_mutex_);
            if (client_fd_ >= 0) close(client_fd_);
            client_fd_ = fd;
        }

        std::cout << "Python client connected\n";

        std::string buffer;
        char chunk[1024];

        while (running_) {
            ssize_t n = recv(fd, chunk, sizeof(chunk), 0);
            if (n <= 0) break;

            buffer.append(chunk, n);

            size_t pos;
            while ((pos = buffer.find('\n')) != std::string::npos) {
                std::string line = buffer.substr(0, pos);
                buffer.erase(0, pos + 1);

                if (!line.empty()) {
                    handle_json_line(line);
                }
            }
        }

        std::cout << "Python client disconnected\n";

        {
            std::lock_guard<std::mutex> lock(socket_mutex_);
            if (client_fd_ == fd) client_fd_ = -1;
        }

        close(fd);
    }

    close(server_fd);
}

void MoosBridge::handle_json_line(const std::string& line) {
    try {
        json j = json::parse(line);

        const std::string message_type = j.value("type", "");
        if (message_type == "query") {
            if (j.value("target", "") == "own_ship") {
                send_own_ship_json();
            } else {
                send_json_line({
                    {"type", "error"},
                    {"error", "unknown query target"},
                    {"target", j.value("target", "")}
                });
            }
            return;
        }

        if (message_type == "commands") {
            const json commands = j.value("commands", json::array());
            if (!commands.is_array() || commands.empty() || commands.size() > 3) {
                throw std::runtime_error("commands must contain between one and three items");
            }
            for (json command : commands) {
                if (!command.is_object()) {
                    throw std::runtime_error("each command must be an object");
                }
                command["type"] = "command";
                handle_json_line(command.dump());
            }
            return;
        }

        if (message_type != "command") {
            return;
        }

        const std::string action_class = j.value("class", "");
        const std::string action_type = j.value("action", "");
        if (action_class == "simulation") {
            const std::string paused = action_type == "pause" ? "true" : "false";
            if (action_type == "pause") {
                comms_.Notify("USM_SIM_PAUSED", paused, MOOSTime());
            } else if (action_type == "resume") {
                comms_.Notify("USM_SIM_PAUSED", paused, MOOSTime());
            } else {
                throw std::runtime_error(
                    "unknown simulation action: " + action_type
                );
            }
            for (auto& client : fleet_comms_) {
                client->Notify("USM_SIM_PAUSED", paused, MOOSTime());
            }
            send_json_line({
                {"type", "simulation_state"},
                {"paused", action_type == "pause"}
            });
            return;
        }
        if (action_class == "communication") {
            if (action_type == "send_vhf") {
                const int channel = j.at("channel").get<int>();
                const std::string message = j.at("message").get<std::string>();
                if (channel < 1 || channel > 88 || message.empty()) {
                    throw std::runtime_error("invalid VHF channel or empty message");
                }
                notify("VHF_TX", json{
                    {"channel", channel},
                    {"message", message}
                }.dump());
            } else if (action_type == "sound_horn") {
                const std::string signal = j.at("signal").get<std::string>();
                static const std::vector<std::string> allowed_signals = {
                    "altering_course_to_starboard",
                    "altering_course_to_port",
                    "doubt_or_danger"
                };
                if (std::find(allowed_signals.begin(), allowed_signals.end(), signal)
                    == allowed_signals.end()) {
                    throw std::runtime_error("unknown horn signal: " + signal);
                }
                notify("SOUND_HORN", json{{"signal", signal}}.dump());
            } else {
                throw std::runtime_error("unknown communication action: " + action_type);
            }
            return;
        }

        Action a;
        a.action_class = j.value("class", "");
        a.type = j.value("action", j.value("type_name", ""));
        a.contact = j.value("contact", "");
        if (a.type.empty()) a.type = j.value("maneuver", "");

        a.degrees = j.value("degrees", 0.0);
        a.distance_m = j.value("distance_m", 200.0);
        a.original_heading = j.value("original_heading", 0.0);
        a.x = j.value("x", 0.0);
        a.y = j.value("y", 0.0);
        a.speed = j.value("speed", -1.0);
        a.completion_radius = j.value("completion_radius", 3.0);
        a.waypoints = parse_waypoints(j.value("waypoints", json::array()));

        if (migrate_deprecated_turn(a)) {
            std::cerr
                << "deprecated turn_port/turn_starboard command migrated to "
                << "signed alter_course\n";
        }

        dispatch_action(a);
    } catch (const std::exception& e) {
        std::cerr << "bad JSON command: " << e.what() << "\n";
    }
}

void MoosBridge::send_state_json() {
    int fd;
    {
        std::lock_guard<std::mutex> lock(socket_mutex_);
        fd = client_fd_;
    }

    if (fd < 0) return;

    OwnshipState s;
    std::string ownship_name;
    std::vector<ContactState> contacts;
    std::optional<Point> mission_objective;
    std::optional<Point> next_waypoint;
    std::optional<Point> temporary_waypoint;
    double target_speed;
    double recovery_speed;
    bool underway;
    bool paused;
    bool mission_complete;
    double minimum_observed_range;
    std::string minimum_range_contact;
    {
        std::lock_guard<std::mutex> lock(state_mutex_);
        s = state_;
        ownship_name = ownship_name_;
        mission_objective = mission_objective_;
        next_waypoint = next_waypoint_;
        temporary_waypoint = temporary_waypoint_;
        target_speed = target_speed_;
        recovery_speed = recovery_speed_;
        paused = paused_;
        mission_complete = mission_complete_;
        minimum_observed_range = minimum_observed_range_;
        minimum_range_contact = minimum_range_contact_;
        underway = current_action_.has_value() && !paused;
        for (const auto& entry : contacts_) {
            contacts.push_back(entry.second);
        }
    }

    json contacts_json = json::array();
    for (const ContactState& contact : contacts) {
        contacts_json.push_back(contact_to_json(s, contact));
    }

    json j = {
        {"type", "state"},
        {"bridge", name_},
        {"vehicle", ownship_name.empty() ? name_ : ownship_name},
        {"vessel_type", s.vessel_type.empty() ? json(nullptr) : json(s.vessel_type)},
        {"ready", s.ready()},
        {"moos_time", s.moos_time},
        {"nav", {
            {"x", s.x},
            {"y", s.y},
            {"heading", s.heading},
            {"speed", s.speed}
        }},
        {"action_underway", underway},
        {"paused", paused},
        {"mission_complete", mission_complete},
        {"minimum_observed_range", std::isfinite(minimum_observed_range)
            ? json(minimum_observed_range) : json(nullptr)},
        {"minimum_range_contact", minimum_range_contact.empty()
            ? json(nullptr) : json(minimum_range_contact)},
        {"target_speed", target_speed},
        {"recovery_speed", recovery_speed},
        {"mission_objective", mission_objective.has_value() ? json{
            {"x", mission_objective->x},
            {"y", mission_objective->y}
        } : json(nullptr)},
        {"next_waypoint", next_waypoint.has_value() ? json{
            {"x", next_waypoint->x},
            {"y", next_waypoint->y}
        } : json(nullptr)},
        {"temporary_waypoint", temporary_waypoint.has_value() ? json{
            {"x", temporary_waypoint->x},
            {"y", temporary_waypoint->y}
        } : json(nullptr)},
        {"contacts", contacts_json}
    };

    send_json_line(j);
}

void MoosBridge::send_own_ship_json() {
    OwnshipState s;
    std::string ownship_name;
    std::vector<ContactState> contacts;
    std::optional<CurrentAction> current_action;
    std::optional<Point> mission_objective;
    std::optional<Point> next_waypoint;
    std::optional<Point> temporary_waypoint;
    double target_speed;
    double recovery_speed;
    bool paused;
    bool mission_complete;
    double minimum_observed_range;
    std::string minimum_range_contact;
    {
        std::lock_guard<std::mutex> lock(state_mutex_);
        s = state_;
        ownship_name = ownship_name_;
        current_action = current_action_;
        mission_objective = mission_objective_;
        next_waypoint = next_waypoint_;
        temporary_waypoint = temporary_waypoint_;
        target_speed = target_speed_;
        recovery_speed = recovery_speed_;
        paused = paused_;
        mission_complete = mission_complete_;
        minimum_observed_range = minimum_observed_range_;
        minimum_range_contact = minimum_range_contact_;
        for (const auto& entry : contacts_) {
            contacts.push_back(entry.second);
        }
    }

    json contacts_json = json::array();
    for (const ContactState& contact : contacts) {
        contacts_json.push_back(contact_to_json(s, contact));
    }

    json current_action_json = nullptr;
    if (current_action.has_value()) {
        json waypoints_json = json::array();
        for (const Point& waypoint : current_action->waypoints) {
            waypoints_json.push_back({
                {"x", waypoint.x},
                {"y", waypoint.y}
            });
        }

        current_action_json = {
            {"class", current_action->action.action_class},
            {"action", current_action->action.type},
            {"degrees", current_action->action.degrees},
            {"speed", current_action->speed},
            {"completion_radius", current_action->completion_radius},
            {"waypoints", waypoints_json}
        };
    }

    json j = {
        {"type", "own_ship"},
        {"bridge", name_},
        {"vehicle", ownship_name.empty() ? name_ : ownship_name},
        {"vessel_type", s.vessel_type.empty() ? json(nullptr) : json(s.vessel_type)},
        {"ready", s.ready()},
        {"moos_time", s.moos_time},
        {"nav", {
            {"x", s.x},
            {"y", s.y},
            {"heading", s.heading},
            {"speed", s.speed}
        }},
        {"action_underway", current_action.has_value() && !paused},
        {"paused", paused},
        {"mission_complete", mission_complete},
        {"minimum_observed_range", std::isfinite(minimum_observed_range)
            ? json(minimum_observed_range) : json(nullptr)},
        {"minimum_range_contact", minimum_range_contact.empty()
            ? json(nullptr) : json(minimum_range_contact)},
        {"target_speed", target_speed},
        {"recovery_speed", recovery_speed},
        {"current_action", current_action_json},
        {"mission_objective", mission_objective.has_value() ? json{
            {"x", mission_objective->x},
            {"y", mission_objective->y}
        } : json(nullptr)},
        {"next_waypoint", next_waypoint.has_value() ? json{
            {"x", next_waypoint->x},
            {"y", next_waypoint->y}
        } : json(nullptr)},
        {"temporary_waypoint", temporary_waypoint.has_value() ? json{
            {"x", temporary_waypoint->x},
            {"y", temporary_waypoint->y}
        } : json(nullptr)},
        {"contacts", contacts_json}
    };

    send_json_line(j);
}

bool MoosBridge::send_json_line(const json& j) {
    std::string line = j.dump() + "\n";

    std::lock_guard<std::mutex> lock(socket_mutex_);
    if (client_fd_ >= 0) {
        ssize_t ignored = send(client_fd_, line.data(), line.size(), MSG_NOSIGNAL);
        (void)ignored;
        return true;
    }
    return false;
}

double MoosBridge::outgoing_moos_time() const {
    std::lock_guard<std::mutex> lock(state_mutex_);
    if (state_.have_moos_time) {
        return state_.moos_time;
    }
    return MOOSTime();
}

bool MoosBridge::notify(const std::string& var, const std::string& val) {
    return comms_.Notify(var, val, outgoing_moos_time());
}

bool MoosBridge::notify(const std::string& var, double val) {
    return comms_.Notify(var, val, outgoing_moos_time());
}

void MoosBridge::dispatch_action(const Action& action) {
    OwnshipState s;
    {
        std::lock_guard<std::mutex> lock(state_mutex_);
        s = state_;
    }

    // Geometry commands need a complete navigation frame. Mode commands do
    // not, so stop/resume remain reliable during bridge/mission startup.
    if (action.action_class == "maneuver" && !s.ready()) {
        std::cerr << "ownship state not ready yet\n";
        return;
    }

    if (action.action_class == "maneuver") {
        if (action.type == "rejoin_route") {
            restore_mission_route();
            return;
        }
        if (action.type == "alter_course") {
            alter_next_route_waypoint(s, action);
            return;
        }
        // Capture relative geometry before releasing the old objective.
        std::vector<Point> maneuver_waypoints_for_action = maneuver_waypoints(s.x, s.y, s.heading, action);
        if (action.type == "reroute" && !action.waypoints.empty()) {
            maneuver_waypoints_for_action = action.waypoints;
        }
        const double maneuver_speed = action.speed > 0.0 ? action.speed : s.speed;

        notify("DEPLOY", "true");
        notify("LOITER", "false");
        notify("STATION_KEEP", "false");
        notify("RETURN", "false");
        notify("LLM_STOP", "false");
        notify("MOOS_MANUAL_OVERRIDE", "false");

        // Force the perpetual waypoint behavior idle for more than one helm
        // tick. This makes a replacement command reset its traversal cleanly.
        notify("LLM_CONTROL", "false");
        notify("LLM_MANEUVER_DONE", "false");
        std::this_thread::sleep_for(std::chrono::milliseconds(650));

        {
            std::lock_guard<std::mutex> lock(state_mutex_);
            paused_ = false;
            current_action_ = CurrentAction{
                action,
                maneuver_waypoints_for_action,
                maneuver_speed,
                action.completion_radius
            };
        }
        publish_waypoints(maneuver_waypoints_for_action, maneuver_speed, action.completion_radius);
    } else if (action.action_class == "speed") {
        if (action.speed > 0.0) {
            bool llm_maneuver_active = false;
            std::vector<Point> remaining_route;
            {
                std::lock_guard<std::mutex> lock(state_mutex_);
                llm_maneuver_active = current_action_.has_value();
                if (current_action_) current_action_->speed = action.speed;
                if (!llm_maneuver_active && s.speed <= 0.2 &&
                    mission_waypoint_index_ >= 0 &&
                    static_cast<size_t>(mission_waypoint_index_) < mission_route_.size()) {
                    size_t next_index = static_cast<size_t>(mission_waypoint_index_);
                    const Point& reported_next = mission_route_[next_index];
                    if (std::hypot(reported_next.x - s.x, reported_next.y - s.y) <= 30.0 &&
                        next_index + 1 < mission_route_.size()) {
                        ++next_index;
                    }
                    remaining_route.assign(
                        mission_route_.begin() + static_cast<std::ptrdiff_t>(next_index),
                        mission_route_.end()
                    );
                }
            }

            std::ostringstream update;
            if (!remaining_route.empty()) {
                update.setf(std::ios::fixed);
                update.precision(1);
                update << "points=";
                for (size_t i = 0; i < remaining_route.size(); ++i) {
                    if (i > 0) update << ":";
                    update << remaining_route[i].x << "," << remaining_route[i].y;
                }
                update << " # speed=" << action.speed;

                // A completed BHV_Waypoint ignores a speed-only update. Reset
                // its completion state by publishing the unvisited route.
                notify("DEPLOY", "true");
                notify("LLM_STOP", "false");
                notify("MOOS_MANUAL_OVERRIDE", "false");
                notify("UP_CROSSING", update.str());
                {
                    std::lock_guard<std::mutex> lock(state_mutex_);
                    mission_route_ = remaining_route;
                    mission_waypoint_index_ = 0;
                    next_waypoint_ = remaining_route.front();
                    temporary_waypoint_.reset();
                }
                std::cout << "restarted stalled mission route: " << update.str() << "\n";
            } else {
                update << "speed=" << action.speed;
                notify(llm_maneuver_active ? "LLM_WPT" : "UP_CROSSING", update.str());
            }
        }
    } else if (action.action_class == "mode") {
        if (action.type == "hold" || action.type == "stop") {
            {
                std::lock_guard<std::mutex> lock(state_mutex_);
                paused_ = true;
            }
            // Preserve LLM_CONTROL and the current waypoint. LLM_STOP selects
            // BHV_FullStop, which keeps the helm/PID engaged and commands a
            // real desired speed of zero. Manual override would only release
            // helm control and can leave stale actuator commands behind.
            notify("LLM_STOP", "true");
            notify("MOOS_MANUAL_OVERRIDE", "false");
            std::cout << "full-stop mode engaged\n";
        } else if (action.type == "resume" || action.type == "resume_course") {
            {
                std::lock_guard<std::mutex> lock(state_mutex_);
                paused_ = false;
            }
            notify("DEPLOY", "true");
            notify("LLM_STOP", "false");
            notify("MOOS_MANUAL_OVERRIDE", "false");
            std::cout << "mission resumed\n";
        } else {
            std::cerr << "unknown mode action: " << action.type << "\n";
        }
    } else {
        std::cerr << "unknown action class: " << action.action_class << "\n";
    }
}

void MoosBridge::alter_next_route_waypoint(const OwnshipState& state, const Action& action) {
    std::vector<Point> route;
    {
        std::lock_guard<std::mutex> lock(state_mutex_);
        route = mission_route_;
    }
    if (route.empty()) {
        throw std::runtime_error("alter_course requires a current mission route");
    }

    // MOOS waypoint-index mail may be absent or lag behind the helm. Infer the
    // active leg from actual position so avoidance can never resume toward a
    // waypoint the vessel has already passed.
    const size_t next_index = active_route_waypoint_index(state.x, state.y, route);
    std::vector<Point> resume_route(
        route.begin() + static_cast<std::ptrdiff_t>(next_index), route.end()
    );
    const Point temporary_target = maneuver_waypoints(
        state.x, state.y, state.heading, action
    ).front();
    std::string contact_name = action.contact;
    double maneuver_speed = state.speed;
    {
        std::lock_guard<std::mutex> lock(state_mutex_);
        if (contact_name.empty()) contact_name = closest_contact_;
        if (target_speed_ > 0.0) maneuver_speed = target_speed_;
    }

    std::ostringstream update;
    update.setf(std::ios::fixed);
    update.precision(1);
    update << "point=" << temporary_target.x << "," << temporary_target.y
           << " # speed=" << maneuver_speed
           << " # radius=" << action.completion_radius;

    notify("DEPLOY", "true");
    notify("LLM_STOP", "false");
    notify("MOOS_MANUAL_OVERRIDE", "false");
    notify("LLM_CONTROL", "false");
    notify("LLM_MANEUVER_DONE", "false");
    std::this_thread::sleep_for(std::chrono::milliseconds(650));
    {
        std::lock_guard<std::mutex> lock(state_mutex_);
        paused_ = false;
        current_action_ = CurrentAction{
            action, {temporary_target}, maneuver_speed, action.completion_radius
        };
        avoidance_ = AvoidanceState{contact_name, resume_route, 0};
        temporary_waypoint_ = temporary_target;
    }
    notify("LLM_WPT", update.str());
    notify("LLM_CONTROL", "true");
    std::cout << "issued temporary course-change waypoint for contact " << contact_name
              << ": " << update.str() << "\n";
}

void MoosBridge::restore_mission_route() {
    std::vector<Point> resume_route;
    double resume_speed = 0.0;
    {
        std::lock_guard<std::mutex> lock(state_mutex_);
        if (!avoidance_ || avoidance_->resume_route.empty()) return;
        resume_route = avoidance_->resume_route;
        if (current_action_) resume_speed = current_action_->speed;
        avoidance_.reset();
        current_action_.reset();
        temporary_waypoint_.reset();
        mission_route_ = resume_route;
        mission_waypoint_index_ = 0;
        next_waypoint_ = resume_route.front();
        paused_ = false;
    }

    std::ostringstream update;
    update.setf(std::ios::fixed);
    update.precision(1);
    update << "points=";
    for (size_t i = 0; i < resume_route.size(); ++i) {
        if (i > 0) update << ":";
        update << resume_route[i].x << "," << resume_route[i].y;
    }
    if (resume_speed > 0.0) update << " # speed=" << resume_speed;
    notify("UP_CROSSING", update.str());
    notify("LLM_STOP", "false");
    notify("LLM_CONTROL", "false");
    notify("LLM_MANEUVER_DONE", "false");
    std::cout << "temporary waypoint completed; restored mission route: " << update.str() << "\n";
}

void MoosBridge::execute_maneuver(const Action& action) {
    OwnshipState s;
    {
        std::lock_guard<std::mutex> lock(state_mutex_);
        s = state_;
    }

    auto waypoints = maneuver_waypoints(s.x, s.y, s.heading, action);
    if (action.type == "reroute" && !action.waypoints.empty()) {
        waypoints = action.waypoints;
    }
    double speed = action.speed > 0.0 ? action.speed : s.speed;

    {
        std::lock_guard<std::mutex> lock(state_mutex_);
        current_action_ = CurrentAction{action, waypoints, speed, action.completion_radius};
    }

    publish_waypoints(waypoints, speed, action.completion_radius);
}

void MoosBridge::publish_waypoints(const std::vector<Point>& waypoints, double speed, double completion_radius) {
    if (waypoints.empty()) {
        std::cerr << "no waypoint generated for maneuver\n";
        return;
    }

    notify("DEPLOY", "true");
    notify("MOOS_MANUAL_OVERRIDE", "false");
    notify("LOITER", "false");
    notify("RETURN", "false");
    notify("STATION_KEEP", "false");

    std::ostringstream update;
    update.setf(std::ios::fixed);
    update.precision(1);
    if (waypoints.size() == 1) {
        const Point& waypoint = waypoints.front();
        update << "point=" << waypoint.x << "," << waypoint.y;
    } else {
        update << "points=";
        for (size_t i = 0; i < waypoints.size(); ++i) {
            if (i > 0) update << ":";
            update << waypoints[i].x << "," << waypoints[i].y;
        }
    }
    update << " # speed=" << speed
           << " # radius=" << completion_radius;

    // Give BHV_Waypoint the new point before enabling LLM_CONTROL so the
    // llm_wpt behavior never activates on its startup/default point.
    notify("LLM_WPT", update.str());
    notify("LLM_CONTROL", "true");

    std::cout << "issued waypoint update: " << update.str() << "\n";
}

double MoosBridge::distance_to_waypoint(const Point& p) const {
    std::lock_guard<std::mutex> lock(state_mutex_);
    return std::hypot(p.x - state_.x, p.y - state_.y);
}

void MoosBridge::finish_maneuver() {
    notify("LLM_CONTROL", "false");
    notify("LLM_MANEUVER_DONE", "true");
    std::cout << "maneuver complete\n";
}
