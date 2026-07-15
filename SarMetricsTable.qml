// SarMetricsTable.qml
//
// Deliberately built from plain Column/Row + Repeater rather than
// QtQuick's TableView component: TableView's API differs meaningfully
// between Qt5 and Qt6, and Harmonic installs can be built against
// either depending on platform — this avoids that whole axis of risk
// for what's a 2-3 row, 7-column display.
//
// "rows" and "elapsed" below are the Q_PROPERTYs from SarMetricsTable.hh,
// read directly (no object prefix) because gz-gui loads this file with
// the plugin's own C++ instance as the QML context object.

import QtQuick 2.9

Rectangle {
  id: root
  color: "transparent"
  implicitWidth: 480
  implicitHeight: header.height + (repeater.count * 22) + footer.height + 24

  property var colWidths: [66, 56, 70, 60, 62, 52, 56]
  property var colLabels: ["Node", "Dist", "Tput", "Ploss", "RSRP", "SINR", "C2"]

  Column {
    anchors.fill: parent
    anchors.margins: 10
    spacing: 6

    Row {
      id: header
      spacing: 6
      Repeater {
        model: colLabels
        delegate: Text {
          width: colWidths[index]
          text: modelData
          font.pixelSize: 11
          font.bold: true
          color: "#9a9d9f"
        }
      }
    }

    Rectangle { width: parent.width; height: 1; color: "#3a3d40" }

    Repeater {
      id: repeater
      model: rows
      delegate: Row {
        spacing: 6

        Text {
          width: colWidths[0]
          text: modelData.label
          font.pixelSize: 12
          font.bold: true
          color: modelData.label === "ALPHA" ? "#5dcaa5"
               : modelData.label === "BRAVO" ? "#85b7eb"
               : "#e6e7e8"
        }
        Text {
          width: colWidths[1]
          text: modelData.d >= 0 ? modelData.d.toFixed(1) + "m" : "--"
          font.pixelSize: 12
          color: "#e6e7e8"
        }
        Text {
          width: colWidths[2]
          text: modelData.tp >= 0 ? modelData.tp.toFixed(1) + "Mbps" : "--"
          font.pixelSize: 12
          color: "#e6e7e8"
        }
        Text {
          width: colWidths[3]
          text: modelData.ploss >= 0 ? modelData.ploss.toFixed(1) + "dB" : "--"
          font.pixelSize: 12
          color: "#e6e7e8"
        }
        Text {
          width: colWidths[4]
          text: modelData.rsrp > -9000 ? modelData.rsrp.toFixed(0) + "dBm" : "--"
          font.pixelSize: 12
          color: "#e6e7e8"
        }
        Text {
          width: colWidths[5]
          text: modelData.sinr > -9000 ? modelData.sinr.toFixed(0) + "dB" : "--"
          font.pixelSize: 12
          color: "#e6e7e8"
        }
        Text {
          width: colWidths[6]
          text: modelData.c2Status === "ok" ? modelData.c2Rtt.toFixed(0) + "ms"
              : modelData.c2Status === "lost" ? "LOST"
              : "n/a"
          font.pixelSize: 12
          color: modelData.c2Status === "ok" ? "#e6e7e8"
               : modelData.c2Status === "lost" ? "#e24b4a"
               : "#6b6e70"
        }
      }
    }

    Text {
      id: footer
      text: "t: " + elapsed.toFixed(1) + "s"
      font.pixelSize: 10
      color: "#6b6e70"
    }
  }
}
