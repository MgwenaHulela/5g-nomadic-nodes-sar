cmake_minimum_required(VERSION 3.10.2 FATAL_ERROR)
project(sar_metrics_table)

# Without this, AUTOMOC silently skips .hh headers (only .h is processed
# by default), so Q_OBJECT in SarMetricsTable.hh never gets moc-generated
# code — which breaks the class's vtable/typeinfo and shows up as
# "undefined symbol: _ZTI<ClassName>" at gz gui dlopen time, not at build
# time (a plain .so build doesn't require every symbol to resolve).
cmake_policy(SET CMP0100 NEW)

# NOTE (verify #2): this assumes Qt5 (gz-gui8/Harmonic on Ubuntu 22.04
# is normally built against Qt5). If your Gazebo install was built
# against Qt6 instead, swap Qt5 -> Qt6 in the two lines below and in
# the target_link_libraries Qt5:: names.
find_package(Qt5 REQUIRED COMPONENTS Core Qml Quick)

find_package(gz-gui8 REQUIRED)
find_package(gz-transport13 REQUIRED)
find_package(gz-msgs10 REQUIRED)
find_package(gz-plugin2 REQUIRED)

set(CMAKE_AUTOMOC ON)
set(CMAKE_AUTORCC ON)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

qt5_add_resources(sar_metrics_table_RCC sar_metrics_table.qrc)

add_library(SarMetricsTable SHARED
  SarMetricsTable.cc
  ${sar_metrics_table_RCC}
)

# NOTE (verify #2, cont'd): these <pkg>::<pkg> imported target names are
# the standard gz-cmake convention across gz-gui/gz-transport/gz-msgs/
# gz-plugin. If CMake errors with "target not found", run
#   find /usr/lib -iname "*gz-gui8*config.cmake*"
# and check the exact exported target name inside that file.
target_link_libraries(SarMetricsTable
  gz-gui8::gz-gui8
  gz-transport13::gz-transport13
  gz-msgs10::gz-msgs10
  gz-plugin2::gz-plugin2
  Qt5::Core
  Qt5::Qml
  Qt5::Quick
)
