# SarMetricsTable

Gazebo GUI plugin: subscribes to `/sar/metrics` (published by the updated
`gazebo_viz.py`) and renders a real table (node, distance, throughput,
path loss, RSRP, SINR, C2 status) instead of the old escaped-text wall in
the TopicEcho panel.

## 1. Build

```bash
cd sar_metrics_table
mkdir build && cd build
cmake ..
make
```

If `cmake ..` fails to find a package, it's almost certainly one of the
two spots flagged inline in `CMakeLists.txt` and `SarMetricsTable.cc` —
Qt5 vs Qt6, or the exact `stringmsg.pb.h` include path for your install.
Fix, rerun `cmake ..`, `make`.

## 2. Point Gazebo at the built plugin

```bash
export GZ_GUI_PLUGIN_PATH=$(pwd)/build
```

(Add this to your shell profile or `start_full_stack.sh` if you want it
picked up automatically every run.)

## 3. Load it

Either from the running GUI's plugin menu (top-right button) — it should
show up as "SarMetricsTable" — or by adding it to a GUI config file
alongside your existing panels:

```xml
<plugin filename="SarMetricsTable">
  <gz-gui>
    <title>SAR metrics table</title>
    <property type="string" key="state">docked</property>
  </gz-gui>
  <topic>/sar/metrics</topic>
</plugin>
```

`<topic>` is optional — it defaults to `/sar/metrics` already.

## 4. Run the updated publisher

```bash
python3 scripts/gazebo_viz.py
```

Same as before — just now emitting JSON instead of ASCII text on the same
topic, so both the old TopicEcho panel (if you keep it open) and this new
table panel will work off the same message stream.
