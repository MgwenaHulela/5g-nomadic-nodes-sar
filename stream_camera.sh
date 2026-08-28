#!/usr/bin/env bash
#
# stream_camera.sh — stream a robot's ACTUAL camera POV over real 5G.
#
# The robot model carries a forward-facing camera (models/sar_ground_*/
# model.sdf); the world's Sensors system renders it to a gz Image topic.
# This pipes that POV — the robot's own view of the simulated scene —
# through an H.264 encoder running INSIDE the robot's netns, so the video
# rides the 5G air interface to a viewer on the host. Drive the robot NLoS
# behind a building and you watch its camera feed go blocky and freeze.
#
#   default ns:  gz_camera_bridge.py (reads gz Image)  --pipe-->
#   robot netns: gst H.264 encoder --> RTP/UDP --> host viewer
#
# Usage:
#   export SAR_SUDO_PASS='...'                 # encoder runs in a netns (root)
#   ./stream_camera.sh [alpha|bravo] [duration_s] [bitrate_kbps]
#
# Requires the stack restarted AFTER the camera was added to the models, so
# the camera topic exists (check: gz topic -l | grep /sar/<robot>/camera).
# Viewer auto-launches if $DISPLAY is set, else the command is printed.
set -uo pipefail
SELF_DIR="$(cd "$(dirname "$0")" && pwd)"

ROBOT="${1:-bravo}"
DURATION="${2:-120}"
KBPS="${3:-1500}"
SENSOR="${4:-rgb}"          # rgb (camera POV) | thermal (heat / victim)
W=320; H=240; FPS=15
case "$ROBOT" in alpha|bravo) ;; *) echo "usage: $0 [alpha|bravo] [dur] [kbps] [rgb|thermal]"; exit 2 ;; esac
case "$SENSOR" in
  rgb)     LINK=camera_link;      SNAME=cam_front; GSTFMT=rgb;   BFMT=rgb;     TAG="CAM" ;;
  thermal) LINK=sar_thermal_link; SNAME=thermal;   GSTFMT=gray8; BFMT=thermal; TAG="THERMAL" ;;
  *) echo "sensor must be rgb or thermal"; exit 2 ;;
esac

if ! sudo -n true 2>/dev/null && [ -z "${SAR_SUDO_PASS:-}" ]; then
  echo "ERROR: the encoder runs inside a network namespace (need root)." >&2
  echo "       export SAR_SUDO_PASS='...' or configure passwordless sudo." >&2
  exit 1
fi

declare -A NS=(  [alpha]=robot_alpha [bravo]=robot_bravo )
declare -A DST=( [alpha]=10.0.0.200  [bravo]=10.0.0.201 )
declare -A PORT=([alpha]=5604        [bravo]=5605 )
ns="${NS[$ROBOT]}"; dst="${DST[$ROBOT]}"; vport="${PORT[$ROBOT]}"
# Camera sensor has no explicit <topic>, so gz auto-scopes it to the model
# name the world assigned (sar_ground_alpha/bravo). The world name is part of
# the topic path, so it must match whichever world is *actually running* — a
# stale ghost topic from a previous world lingers in `gz topic -l` and will
# happily accept a subscription while publishing nothing (black/frozen feed).
# So: honour SAR_WORLD if set, else auto-detect the live world from a topic
# that carries frames for THIS robot's camera.
TOPICS="$(gz topic -l 2>/dev/null)"
if [ -n "${SAR_WORLD:-}" ]; then
  WORLD="$SAR_WORLD"
else
  # Candidate worlds that expose this robot's camera image topic.
  CANDIDATES="$(printf '%s\n' "$TOPICS" \
    | sed -n "s#^/world/\([^/]*\)/model/sar_ground_${ROBOT}/link/${LINK}/sensor/${SNAME}/image\$#\1#p")"
  # Prefer a candidate whose model is fully loaded (has a lidar scan topic) — a
  # stale ghost from a previous world carries only the lone camera topic.
  WORLD=""
  for w in $CANDIDATES; do
    if printf '%s\n' "$TOPICS" | grep -qx "/world/$w/model/sar_ground_${ROBOT}/link/sar_lidar_link/sensor/lidar/scan"; then
      WORLD="$w"; break
    fi
  done
  WORLD="${WORLD:-$(printf '%s\n' "$CANDIDATES" | head -n1)}"
  WORLD="${WORLD:-sar_urban}"   # fall back to the historical default
fi
topic="/world/${WORLD}/model/sar_ground_${ROBOT}/link/${LINK}/sensor/${SNAME}/image"

# camera topic present?
if ! printf '%s\n' "$TOPICS" | grep -qx "$topic"; then
  echo "WARNING: $topic not found. Restart the stack so the camera-equipped" >&2
  echo "         model reloads (SAR_WORLD=$WORLD ./start_full_stack.sh)." >&2
fi

# askpass: sudo can't read the password from stdin (the video pipe owns it)
ASKPASS="$(mktemp)"
printf '#!/bin/sh\nprintf "%%s\\n" "$SAR_SUDO_PASS"\n' > "$ASKPASS"
chmod +x "$ASKPASS"
export SUDO_ASKPASS="$ASKPASS" SAR_SUDO_PASS="${SAR_SUDO_PASS:-}"

RX_CAPS="application/x-rtp,media=video,encoding-name=H264,payload=96"
VIEWER=( gst-launch-1.0 -e udpsrc address="$dst" port="$vport" caps="$RX_CAPS"
         ! rtpjitterbuffer latency=150 ! rtph264depay ! avdec_h264
         ! videoconvert ! autovideosink sync=false )

RX_PID=""
cleanup() {
  echo; echo "[stream_camera] stopping..."
  [ -n "$RX_PID" ] && kill "$RX_PID" 2>/dev/null
  sudo -A pkill -f "udpsink host=$dst port=$vport" 2>/dev/null
  pkill -f "gz_camera_bridge.py --topic $topic" 2>/dev/null
  rm -f "$ASKPASS"; wait 2>/dev/null
}
trap cleanup INT TERM EXIT

if [ -n "${DISPLAY:-}" ]; then
  "${VIEWER[@]}" >/tmp/stream_camera_rx.log 2>&1 &
  RX_PID=$!
  echo "[stream_camera] viewer window opening (${ROBOT^^} camera POV)..."
else
  echo "[stream_camera] no \$DISPLAY — run the viewer where you have a screen:"
  printf '   '; printf '%q ' "${VIEWER[@]}"; echo
fi
sleep 1

echo "[stream_camera] ${ROBOT^^} ${SENSOR}: -> H.264 ${KBPS}kbps -> 5G -> $dst:$vport (${DURATION}s)"
# bridge (default ns, reads gz camera) | encoder (robot netns, over the air)
timeout "$DURATION" python3 "$SELF_DIR/scripts/gz_camera_bridge.py" \
      --topic "$topic" --width "$W" --height "$H" --format "$BFMT" \
  | sudo -A ip netns exec "$ns" \
      gst-launch-1.0 -e fdsrc fd=0 \
        ! rawvideoparse width=$W height=$H format=$GSTFMT framerate=$FPS/1 \
        ! videoconvert \
        ! textoverlay text="SAR ${ROBOT^^} ${TAG}" valignment=top halignment=left font-desc="Sans, 18" \
        ! timeoverlay valignment=bottom halignment=right \
        ! x264enc tune=zerolatency bitrate=$KBPS speed-preset=ultrafast key-int-max=$FPS \
        ! rtph264pay config-interval=1 pt=96 \
        ! udpsink host="$dst" port="$vport"
echo "[stream_camera] finished."
