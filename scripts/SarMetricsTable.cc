/*
 * SarMetricsTable.cc — see SarMetricsTable.hh for design notes.
 *
 * Data is pushed to QML via named QQmlContext properties (SarRows,
 * SarElapsed), matching gz-gui's own TopicEcho pattern (TopicEchoMsgList)
 * rather than a bare Q_PROPERTY the QML file reads unqualified — the
 * latter throws "ReferenceError: X is not defined" in this gz-gui
 * version, confirmed by actually running it.
 */
#include <mutex>

#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonValue>
#include <QQmlContext>
#include <QQmlApplicationEngine>
#include <QVariantMap>

#include <gz/common/Console.hh>
#include <gz/msgs/stringmsg.pb.h>
#include <gz/plugin/Register.hh>
#include <gz/transport/Node.hh>

#include <gz/gui/Application.hh>

#include "SarMetricsTable.hh"

namespace
{
  // Sentinels mirrored from gazebo_viz.py's JSON payload. Real distance /
  // throughput / ploss / C2 RTT are never negative; real RSRP/SINR are
  // (roughly -150..0), so they need a sentinel far outside that range.
  constexpr double kMissing = -1.0;
  constexpr double kMissingDbm = -9999.0;

  QVariantList EmptyRows()
  {
    return QVariantList();
  }
}

class SarMetricsTablePrivate
{
  /// \brief Topic
  public: QString topic{"/sar/metrics"};

  /// \brief Latest parsed rows (list of QVariantMap)
  public: QVariantList rows;

  /// \brief Latest elapsed sim time from the message
  public: double elapsed{0.0};

  /// \brief Protects rows/elapsed, which are written on the transport
  /// thread (OnMessage) and read on the Qt/GUI thread (OnNewData).
  public: std::mutex mutex;

  /// \brief Node for communication
  public: gz::transport::Node node;
};

/////////////////////////////////////////////////
SarMetricsTable::SarMetricsTable()
  : gz::gui::Plugin(), dataPtr(new SarMetricsTablePrivate)
{
  // Register the context properties up front (before LoadConfig/QML
  // load), same timing TopicEcho uses for TopicEchoMsgList, so the QML
  // file never sees an undefined identifier even before the first
  // message arrives.
  auto context = gz::gui::App()->Engine()->rootContext();
  context->setContextProperty("SarRows", EmptyRows());
  context->setContextProperty("SarElapsed", 0.0);
}

/////////////////////////////////////////////////
SarMetricsTable::~SarMetricsTable() = default;

/////////////////////////////////////////////////
void SarMetricsTable::LoadConfig(const tinyxml2::XMLElement *_pluginElem)
{
  if (this->title.empty())
    this->title = "SAR metrics table";

  // Optional <topic>/my/topic</topic> override, direct child of <plugin>,
  // sibling to the <gz-gui> title/size block (same convention HelloPlugin
  // uses for its <message> tag).
  if (_pluginElem)
  {
    auto topicElem = _pluginElem->FirstChildElement("topic");
    if (nullptr != topicElem && nullptr != topicElem->GetText())
      this->dataPtr->topic = QString(topicElem->GetText());
  }

  this->connect(this, SIGNAL(NewData()), this, SLOT(OnNewData()),
      Qt::QueuedConnection);

  auto topicStd = this->dataPtr->topic.toStdString();
  if (!this->dataPtr->node.Subscribe(
      topicStd, &SarMetricsTable::OnMessage, this))
  {
    gzerr << "Failed to subscribe to [" << topicStd << "]" << std::endl;
  }
}

/////////////////////////////////////////////////
void SarMetricsTable::OnMessage(const gz::msgs::StringMsg &_msg)
{
  // Runs on the gz-transport thread. Parse, stash behind the mutex, then
  // hop onto the Qt/GUI thread via the queued NewData -> OnNewData
  // connection before touching QQmlContext (which is not thread-safe).
  auto doc = QJsonDocument::fromJson(
      QByteArray::fromStdString(_msg.data()));
  if (!doc.isObject())
    return;
  auto obj = doc.object();

  QVariantList newRows;
  auto nodesVal = obj.value("nodes");
  if (nodesVal.isArray())
  {
    for (const auto &nodeVal : nodesVal.toArray())
    {
      if (!nodeVal.isObject())
        continue;
      auto n = nodeVal.toObject();

      QVariantMap row;
      row["label"] = n.value("label").toString();
      row["d"] = n.value("d").toDouble(kMissing);
      row["tp"] = n.value("tp_mbps").toDouble(kMissing);
      row["ploss"] = n.value("ploss_db").toDouble(kMissing);
      row["rsrp"] = n.value("rsrp_dbm").toDouble(kMissingDbm);
      row["sinr"] = n.value("sinr_db").toDouble(kMissingDbm);
      row["c2Rtt"] = n.value("c2_rtt_ms").toDouble(kMissing);
      row["c2Status"] = n.value("c2_status").toString("na");
      newRows.append(row);
    }
  }

  {
    std::lock_guard<std::mutex> lock(this->dataPtr->mutex);
    this->dataPtr->rows = newRows;
    this->dataPtr->elapsed = obj.value("t").toDouble(0.0);
  }
  this->NewData();
}

/////////////////////////////////////////////////
void SarMetricsTable::OnNewData()
{
  // Now on the GUI thread: safe to touch the QML engine's context.
  QVariantList rows;
  double elapsed;
  {
    std::lock_guard<std::mutex> lock(this->dataPtr->mutex);
    rows = this->dataPtr->rows;
    elapsed = this->dataPtr->elapsed;
  }
  auto context = gz::gui::App()->Engine()->rootContext();
  context->setContextProperty("SarRows", rows);
  context->setContextProperty("SarElapsed", elapsed);
}

/////////////////////////////////////////////////
QString SarMetricsTable::Topic() const
{
  return this->dataPtr->topic;
}

/////////////////////////////////////////////////
void SarMetricsTable::SetTopic(const QString &_topic)
{
  {
    std::lock_guard<std::mutex> lock(this->dataPtr->mutex);
    for (auto const &sub : this->dataPtr->node.SubscribedTopics())
      this->dataPtr->node.Unsubscribe(sub);
    this->dataPtr->topic = _topic;
  }

  auto topicStd = _topic.toStdString();
  if (!this->dataPtr->node.Subscribe(
      topicStd, &SarMetricsTable::OnMessage, this))
  {
    gzerr << "Failed to subscribe to [" << topicStd << "]" << std::endl;
  }
  this->TopicChanged();
}

// Register this plugin.
GZ_ADD_PLUGIN(SarMetricsTable, gz::gui::Plugin)
