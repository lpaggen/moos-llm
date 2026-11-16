cmake_minimum_required(VERSION 3.16)
project(pLLMBridge LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

find_package(nlohmann_json REQUIRED)
find_package(MOOS REQUIRED)

add_executable(pLLMBridge
  src/main.cpp
  src/MoosBridge.cpp
)

target_include_directories(pLLMBridge PRIVATE
  headers
  ${MOOS_INCLUDE_DIRS}
)

target_link_libraries(pLLMBridge
  nlohmann_json::nlohmann_json
  ${MOOS_LIBRARIES}
  pthread
)