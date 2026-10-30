# AirPlay capture plan (GPT/Codex, 2026-10-29)

Generated with: codex exec --sandbox read-only (stdin closed). Deviation by Claude: UxPlay -mp4 is the primary recorder, -vdmp raw dump kept only as a backup, because AirPlay sends frames on change and a fixed -r 60 remux would compress static stretches.

Read both files. No files changed.

1. **Add mirroring helpers to `agent-workspace/agent_helpers.py`.**
   - Define named constants for the candidate `"Screen Mirroring"` / `"Stop Mirroring"` labels, element types, known state values, polling intervals, and deadlines. Mark all mirroring selectors and values **unverified on iOS 27**.
   - Refactor Control Center opening/closing so it no longer depends on `_RECORD_CHAIN`; retain the measured short pull and bottom-area dismissal.
   - Implement `start_mirroring(receiver_name)`: open Control Center, open the mirroring picker, select the exact receiver, and verify a device-confirmed connected state for that receiver.
   - Implement `stop_mirroring()`: open the picker, stop mirroring, and verify disconnection.
   - Use only type-bounded class chains through `find_first`, attribute reads, validated rectangles, and existing gesture helpers. Escape receiver names in predicates. Avoid `/source`, `**/*`, and `wait_stable()` in Control Center.
   - Fail loudly on unknown labels, values, missing receivers, or timeouts. Close picker and Control Center in cleanup after confirming they opened; preserve the primary error if cleanup also fails.

2. **Create `C:/CaptureRig/airplay/capture_uxplay.ps1`.**
   - Pin an MSYS2 UCRT64 UxPlay build; preflight its DLLs/plugins, discovery support, ffmpeg, and output directories.
   - Start the receiver hidden, with logs and a unique working directory per take:
     ```text
     uxplay.exe -n <name> -nh -s <WxH> -fps 60 -vdmp
     ```
   - Expose a bounded ready/stop/result protocol to Python. Distinguish receiver readiness from actual incoming video; require a growing dump before replay.
   - On stop, gracefully terminate the owned receiver, wait for dump closure, then remux:
     ```text
     ffmpeg -fflags +genpts -r 60 -f h264 -i videodump.h264 -c copy -movflags +faststart <timestamp>.partial.mp4
     ```
   - Check exit status and probe the result before renaming into `C:/CaptureRig/takes/<timestamp>.mp4`; retain raw data/logs on failure. This is video-only, and the assumed input cadence needs validation: `-fps 60` specifies a maximum. [UxPlay documentation](https://github.com/FDH2/UxPlay/blob/master/uxplay.1)

3. **Create `capture_window.ps1` as an explicit fallback.**
   - Accept an exact ApowerMirror window title and use the same lifecycle/output protocol:
     ```text
     ffmpeg -f gdigrab -framerate 60 -draw_mouse 0 -i "title=<window>" -an -c:v h264_nvenc -pix_fmt yuv420p <timestamp>.partial.mp4
     ```
   - Preflight the window and NVENC availability; handle odd dimensions, stop through ffmpeg’s `q`, and validate before final rename. [FFmpeg window capture documentation](https://ffmpeg.org/ffmpeg-devices.html#gdigrab)

4. **Add `replay_take_airplay(path, receiver)`.**
   - Validate the take before starting capture; share existing validation with `replay_take`.
   - Start capture → await receiver readiness → start mirroring → await video → confirm Control Center closed → call `replay_take(path, record=False)`.
   - In `finally`, independently attempt mirroring shutdown and capture finalization, including after partial startup failures. Preserve replay errors and report cleanup failures.
   - Return the finalized MP4 path and replay duration. Keep the existing coordinate-only replay loop and idle-wait restoration.

5. **Validate failure handling without a phone**, then run a short device take: selector misses, unexpected states, connection timeout, capture death, replay exception, and teardown/remux failure.

Open questions to test on device:

- What are the actual mirroring control, receiver-row, connected-state, and stop selectors on iOS 27?
- How are nested picker and Control Center reliably dismissed?
- Does receiver discovery work consistently with the selected build, network, and Windows firewall?
- What resolution and frame cadence are negotiated? Does remuxed duration match wall-clock duration during static screens?
- Do rotation, reconnects, or SPS/PPS changes disrupt the dump?
- Can UxPlay shut down gracefully from a hidden Windows process and flush reliably?
- Does ApowerMirror capture remain correct when covered, minimized, resized, or rotated?
- How much Control Center footage needs trimming, and is audio required?
tokens used
