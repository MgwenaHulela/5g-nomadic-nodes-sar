/*
 * SarMetricsTable.cc — see SarMetricsTable.hh for design notes.
 *
 * NOTE: this has been written against the documented gz-gui8/Harmonic
 * plugin API (cross-checked against gz-gui's real TopicEcho.cc and
 * HelloPlugin.cc), but has not been compiled here — no network access to
 * the OSRF package repo in this environment. Build it on your machine and
 * treat any compiler error as the next debugging step, not a sign the
 * whole approach is wrong. The two spots most likely to need a tweak for
 * your exact install are marked below.
 */
#include <mutex>

#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonValue>
#include <QVariantMap>

#include <gz/common/Console.hh>
#include <gz/msgs/stringmsg.pb.h>
#include <gz/plugin/Register.hh>
#include <gz/transport/Node.hh>

#include "SarMetricsTable.hh"

namespace
{
  // Sentinels mirrored from gazebo_viz.py's JSON payload. Real distance /
  // throughput / ploss / C2 RTT are never negative; real RSRP/SINR are
  // (roughly -150..0), so they need a sentinel far outside that range.
  constexpr double kMissing = -1.0;
  constexpr double kMissingDbm = -9999.0;
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
  /// thread (OnMessage) and read on the Qt/GUI thread (Rows/Elapsed).
  public: std::mutex mutex;

  /// \brief Node for communication
  public: gz::transport::Node node;
};

/////////////////////////////////////////////////
SarMetricsTable::SarMetricsTable()
  : gz::gui::Plugin(), dataPtr(new SarMetricsTablePrivate)
{
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
  // connection before touching anything QML-visible.
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
  // Now on the GUI thread: safe to notify QML's bindings.
  this->RowsChanged();
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

/////////////////////////////////////////////////
QVariantList SarMetricsTable::Rows() const
{
  std::lock_guard<std::mutex> lock(this->dataPtr->mutex);
  return this->dataPtr->rows;
}

/////////////////////////////////////////////////
double SarMetricsTable::Elapsed() const
{
  std::lock_guard<std::mutex> lock(this->dataPtr->mutex);
  return this->dataPtr->elapsed;
}

// Register this plugin.
// NOTE (verify #1): if this fails to link/load, double check that
// gz::msgs::StringMsg's protobuf header is really at
// <gz/msgs/stringmsg.pb.h> for your install — some Harmonic package
// layouts expose it as <gz/msgs10/stringmsg.pb.h> instead.
GZ_ADD_PLUGIN(SarMetricsTable, gz::gui::Plugin)
