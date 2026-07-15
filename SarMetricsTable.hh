/*
 * SarMetricsTable — Gazebo GUI plugin.
 *
 * Subscribes to /sar/metrics (a gz.msgs.StringMsg carrying a JSON payload,
 * see gazebo_viz.py's update_viz()) and exposes the parsed fields to QML
 * as a plain list of rows, so SarMetricsTable.qml can render a real table
 * instead of a wall of preformatted text.
 *
 * Modeled directly on gz-gui's own TopicEcho plugin (subscribe -> mutex ->
 * queued-signal marshal onto the Qt/GUI thread -> update a Q_PROPERTY that
 * QML reads) and HelloPlugin (minimal LoadConfig/XML-config skeleton).
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

  /// \brief Parsed rows, one QVariantMap per node (ALPHA, BRAVO, ...), each
  /// with keys: label, d, tp, ploss, rsrp, sinr, c2Rtt, c2Status.
  /// Missing readings arrive as sentinel values (-1 for non-negative
  /// fields like distance/throughput/ploss/RTT, -9999 for RSRP/SINR which
  /// are legitimately negative) — SarMetricsTable.qml checks these to
  /// show "--" instead of a bogus number.
  Q_PROPERTY(
    QVariantList rows
    READ Rows
    NOTIFY RowsChanged
  )

  /// \brief Elapsed sim time reported in the last message, for the small
  /// "t: NN.Ns" footer.
  Q_PROPERTY(
    double elapsed
    READ Elapsed
    NOTIFY RowsChanged
  )

  /// \brief Constructor
  public: SarMetricsTable();

  /// \brief Destructor
  public: ~SarMetricsTable() override;

  // Documentation inherited
  public: void LoadConfig(const tinyxml2::XMLElement *_pluginElem) override;

  /// \brief Get the topic as a string, for the Properties red QML side.
  public: QString Topic() const;

  /// \brief Set the topic to subscribe to. Unsubscribes from the previous
  /// topic first.
  public: void SetTopic(const QString &_topic);

  /// \brief Get the current parsed rows.
  public: QVariantList Rows() const;

  /// \brief Get the last reported elapsed sim time.
  public: double Elapsed() const;

  signals: void TopicChanged();

  signals: void RowsChanged();

  /// \brief Internal signal used to marshal the transport-thread callback
  /// onto the Qt/GUI thread via a queued connection (same pattern as
  /// TopicEcho's AddMsg/OnAddMsg).
  signals: void NewData();

  private slots: void OnNewData();

  /// \brief Callback on the gz-transport thread. Do not touch QML/Qt
  /// state directly here — stash the parsed data behind the mutex and
  /// emit NewData(), which OnNewData() (queued, GUI thread) picks up.
  private: void OnMessage(const gz::msgs::StringMsg &_msg);

  /// \internal
  private: std::unique_ptr<SarMetricsTablePrivate> dataPtr;
};

#endif  // SAR_METRICS_TABLE_HH_
