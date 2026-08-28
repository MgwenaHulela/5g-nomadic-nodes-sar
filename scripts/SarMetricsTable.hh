/*
 * SarMetricsTable — Gazebo GUI plugin.
 *
 * Subscribes to /sar/metrics (a gz.msgs.StringMsg carrying a JSON payload,
 * see gazebo_viz.py's update_viz()) and exposes the parsed fields to QML.
 *
 * Data reaches QML via named QQmlContext properties ("SarRows",
 * "SarElapsed") set from Application::Engine()->rootContext(), the same
 * mechanism gz-gui's own TopicEcho plugin uses for its message list
 * (TopicEchoMsgList) — confirmed against TopicEcho.cc rather than assumed.
 * An earlier version of this file tried exposing plain Q_PROPERTY values
 * for direct bare-name access in QML; that does not work in gz-gui's
 * plugin-loading model (it throws "ReferenceError: X is not defined") —
 * this version replaces that approach.
 */
#ifndef SAR_METRICS_TABLE_HH_
#define SAR_METRICS_TABLE_HH_

#include <memory>

#include <gz/gui/qt.h>
#include <gz/gui/Plugin.hh>

namespace gz
{
  namespace msgs
  {
    class StringMsg;
  }
}

class SarMetricsTablePrivate;

class SarMetricsTable : public gz::gui::Plugin
{
  Q_OBJECT

  /// \brief Topic this plugin listens on. Defaults to /sar/metrics but can
  /// be overridden from a GUI config file with:
  ///   <plugin filename="SarMetricsTable"><topic>/some/topic</topic></plugin>
  Q_PROPERTY(
    QString topic
    READ Topic
    WRITE SetTopic
    NOTIFY TopicChanged
  )

  /// \brief Constructor
  public: SarMetricsTable();

  /// \brief Destructor
  public: ~SarMetricsTable() override;

  // Documentation inherited
  public: void LoadConfig(const tinyxml2::XMLElement *_pluginElem) override;

  /// \brief Get the topic as a string.
  public: QString Topic() const;

  /// \brief Set the topic to subscribe to. Unsubscribes from the previous
  /// topic first.
  public: void SetTopic(const QString &_topic);

  signals: void TopicChanged();

  /// \brief Internal signal used to marshal the transport-thread callback
  /// onto the Qt/GUI thread via a queued connection (same pattern as
  /// TopicEcho's AddMsg/OnAddMsg). QML/Qt-context updates must happen on
  /// the GUI thread, not the gz-transport callback thread.
  signals: void NewData();

  /// \brief Runs on the GUI thread. Pushes the latest parsed data into
  /// the "SarRows" / "SarElapsed" QQmlContext properties that
  /// SarMetricsTable.qml reads.
  private slots: void OnNewData();

  /// \brief Callback on the gz-transport thread. Do not touch QML/Qt
  /// state directly here — stash the parsed data behind the mutex and
  /// emit NewData(); OnNewData() (queued, GUI thread) does the actual
  /// QQmlContext update.
  private: void OnMessage(const gz::msgs::StringMsg &_msg);

  /// \internal
  private: std::unique_ptr<SarMetricsTablePrivate> dataPtr;
};

#endif  // SAR_METRICS_TABLE_HH_
