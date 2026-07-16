#!/usr/bin/env bash
#
# stream_video.sh — WATCHABLE H.264 video from a SAR robot over real 5G.
#
# Unlike stream_demo.sh (which streams synthetic packets to measure QoS),
# this sends an actual H.264/RTP video flow you can watch decode live — so
# you literally see it go blocky / freeze as the robot moves NLoS behind a
# building. The encoder runs INSIDE the robot's netns, so the video rides
# the 5G air interface (rfsim gNB -> UPF) to a viewer on the host.
#
# Source is a GStreamer test pattern (a moving ball) with a robot-ID + clock
# overlay — a stand-in for a camera. To stream the robot's actual point of
# view of the simulated scene, add a Gazebo camera sensor to its model and
# feed that gz image topic in here instead (see README / ask).
#
# Usage:
#   export SAR_SUDO_PASS='...'                 # sender runs in a netns (root)
#   ./stream_video.sh [alpha|bravo] [duration_s] [bitrate_kbps]
#   ./stream_video.sh bravo 120 1500
#
# The viewer auto-launches if $DISPLAY is set; otherwise the exact viewer
# command is printed so you can run it wherever you have a screen.
set -uo pipefail
SELF_DIR="$(cd "$(dirname "$0")" && pwd)"

ROBOT="${1:-bravo}"
DURATION="${2:-120}"
KBPS="${3:-1500}"

if ! sudo -n true 2>/dev/null && [ -z "${SAR_SUDO_PASS:-}" ]; then
  echo "ERROR: the encoder runs inside a network namespace (need root)." >&2
  echo "       export SAR_SUDO_PASS='...' or configure passwordless sudo." >&2
  exit 1
fi
sudo_run() { echo "${SAR_SUDO_PASS:-}" | sudo -S -p '' "$@"; }

declare -A NS=(  [alpha]=robot_alpha [bravo]=robot_bravo )
declare -A DST=( [alpha]=10.0.0.200  [bravo]=10.0.0.201 )
declare -A PORT=([alpha]=5602        [bravo]=5603 )
case "$ROBOT" in alpha|bravo) ;; *) echo "usage: $0 [alpha|bravo] [dur] [kbps]"; exit 2 ;; esac

dst="${DST[$ROBOT]}"; vport="${PORT[$ROBOT]}"; ns="${NS[$ROBOT]}"

RX_CAPS="application/x-rtp,media=video,encoding-name=H264,payload=96"
VIEWER=( gst-launch-1.0 -e udpsrc address="$dst" port="$vport" caps="$RX_CAPS"
         ! rtpjitterbuffer latency=150 ! rtph264depay ! avdec_h264
         ! videoconvert ! autovideosink sync=false )

RX_PID=""
cleanup() {
  echo; echo "[stream_video] stopping..."
  [ -n "$RX_PID" ] && kill "$RX_PID" 2>/dev/null
  sudo_run pkill -f "udpsink host=$dst port=$vport" 2>/dev/null
  wait 2>/dev/null
}
trap cleanup INT TERM EXIT

# Viewer on the host (needs a display). Auto-start if we have one.
if [ -n "${DISPLAY:-}" ]; then
  "${VIEWER[@]}" >/tmp/stream_video_rx.log 2>&1 &
  RX_PID=$!
  echo "[stream_video] viewer window opening (${ROBOT^^} cam)..."
else
  echo "[stream_video] no \$DISPLAY here — run the viewer where you have a screen:"
  printf '   '; printf '%q ' "${VIEWER[@]}"; echo
fi
sleep 1

# Encoder/sender inside the robot's netns -> over the air.
echo "[stream_video] ${ROBOT^^}: H.264 ${KBPS}kbps over 5G -> $dst:$vport for ${DURATION}s"
timeout "$DURATION" bash -c "$(cat <<EOF
echo '${SAR_SUDO_PASS:-}' | sudo -S -p '' ip netns exec $ns \
  gst-launch-1.0 -e videotestsrc pattern=ball is-live=true \
    ! 'video/x-raw,width=640,height=480,framerate=15/1' \
    ! timeoverlay ! textoverlay text='SAR ${ROBOT^^} CAM' valignment=top halignment=left font-desc='Sans, 20' \
    ! x264enc tune=zerolatency bitrate=$KBPS speed-preset=ultrafast key-int-max=15 \
    ! rtph264pay config-interval=1 pt=96 \
    ! udpsink host=$dst port=$vport
EOF
)"
echo "[stream_video] sender finished."
