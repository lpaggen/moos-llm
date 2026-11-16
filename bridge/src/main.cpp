#include "MoosBridge.h"

#include "MOOS/libMOOS/Utils/MOOSUtilityFunctions.h"

#include <exception>
#include <iostream>
#include <sstream>
#include <vector>

int main(int argc, char** argv) {
    if (argc < 4 || argc > 6) {
        std::cerr << "usage: pLLMBridge <moos_host> <moos_port> <tcp_port> "
                     "[time_warp] [fleet_ports_csv]\n";
        return 1;
    }

    std::string moos_host = argv[1];
    int moos_port = 0;
    int tcp_port = 0;
    double time_warp = 1.0;
    std::vector<int> fleet_ports;

    try {
        moos_port = std::stoi(argv[2]);
        tcp_port = std::stoi(argv[3]);
        if (argc == 5) {
            time_warp = std::stod(argv[4]);
        }
        if (argc == 6) {
            time_warp = std::stod(argv[4]);
            std::stringstream ports(argv[5]);
            std::string item;
            while (std::getline(ports, item, ',')) {
                if (!item.empty()) fleet_ports.push_back(std::stoi(item));
            }
        }
    } catch (const std::exception& e) {
        std::cerr << "invalid numeric argument: " << e.what() << "\n";
        return 1;
    }

    if (time_warp <= 0.0) {
        std::cerr << "time_warp must be positive\n";
        return 1;
    }

    SetMOOSTimeWarp(time_warp);
    std::cout << "MOOS time warp set to " << GetMOOSTimeWarp() << "\n";

    MoosBridge bridge(
        "llm_bridge", moos_host, moos_port, tcp_port, std::move(fleet_ports)
    );

    if (!bridge.start()) {
        return 1;
    }

    bridge.run();
    return 0;
}
