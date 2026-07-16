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
W=320; H=240; FPS=15
case "$ROBOT" in alpha|bravo) ;; *) echo "usage: $0 [alpha|bravo] [dur] [kbps]"; exit 2 ;; esac

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
# name the world assigned (sar_ground_alpha/bravo). Override with SAR_WORLD.
WORLD="${SAR_WORLD:-sar_urban}"
topic="/world/${WORLD}/model/sar_ground_${ROBOT}/link/camera_link/sensor/cam_front/image"

# camera topic present?
if ! gz topic -l 2>/dev/null | grep -qx "$topic"; then
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

echo "[stream_camera] ${ROBOT^^}: camera POV -> H.264 ${KBPS}kbps -> 5G -> $dst:$vport (${DURATION}s)"
# bridge (default ns, reads gz camera) | encoder (robot netns, over the air)
timeout "$DURATION" python3 "$SELF_DIR/scripts/gz_camera_bridge.py" \
      --topic "$topic" --width "$W" --height "$H" \
  | sudo -A ip netns exec "$ns" \
      gst-launch-1.0 -e fdsrc fd=0 \
        ! rawvideoparse width=$W height=$H format=rgb framerate=$FPS/1 \
        ! videoconvert \
        ! textoverlay text="SAR ${ROBOT^^} CAM" valignment=top halignment=left font-desc="Sans, 18" \
        ! timeoverlay valignment=bottom halignment=right \
        ! x264enc tune=zerolatency bitrate=$KBPS speed-preset=ultrafast key-int-max=$FPS \
        ! rtph264pay config-interval=1 pt=96 \
        ! udpsink host="$dst" port="$vport"
echo "[stream_camera] finished."
