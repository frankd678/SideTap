   ```text
   validate take
   optionally start_recording()
   set waitForIdleTimeout to 0
   try:
       execute saved coordinate steps and delays
   finally:
       restore config.WDA_IDLE_WAIT
       optionally stop_recording()
   ```

   Use nested cleanup so stopping a recording is still attempted if restoring settings fails. Track whether this invocation successfully started recording, so it never stops a pre-existing recording.

   For predictable replay timing, abort if setting zero fails. Restore in `finally` after a successful setting change, following `_human_gesture`’s shared-session discipline. Preserve the original replay error if cleanup also fails, while reporting the cleanup failure.

   The recording wrapper necessarily performs its targeted verification reads outside the coordinate loop. Also note that WDA session replacement reapplies default settings: a long pause or session recovery can reset zero idle wait. If supporting such takes, reassert zero before subsequent gestures without adding screen reads.

9. **Verify the behavior with isolated fakes, then an on-device rehearsal.**

   Check that:

   - Recording uses the typed class chain, rectangle endpoint, and fresh post-tap value; no `/source` call occurs.
   - Missing controls, already-on/off states, failed toggles, and exceptions close Control Center.
   - Dry runs resolve each tap once and save the coordinates actually used.
   - Replay succeeds with all perception helpers configured to raise if called.
   - Idle wait restores after success, gesture failure, and interruption.
   - Optional recording cleanup runs after replay failure.
   - Invalid take files fail before any phone action.

   Finally, measure start/stop latency on iOS 27 and replay a short take twice from the same initial screen. Keep validation code transient or within the workspace constraint; leave `src/` untouched.
tokens used
36,341
Read the requested files, plus the namespace loader in `run.py` and `viewer._human_gesture`. No files were edited.

1. **Keep all implementation in `agent-workspace/agent_helpers.py`.**

   Use the injected `client()`, `tap()`, `swipe()`, `find_text()`, `screen_info()`, and `WDAError`. Add only standard-library imports for JSON/path handling and an explicit `from phone_harness import config`: the loader injects `helpers.__all__`, which does **not** include `config`. No `src/` changes or new dependencies are needed.

2. **Replace `_record_button()`’s tree traversal with a bounded lookup.**

   Resolve only this element type and exact label:

   ```python
   '**/XCUIElementTypeButton[`label == "Screen Recording"`]'
   ```

   Call `c.find_first(chain)`, then `c.element_value(eid)`. Distinguish a missing element (`None`) from an existing button whose value is empty. Never fall back to `ui_tree()` or an unbounded `**/*` query.

   This preserves the existing protection against matching “Screen Recording” in the Dynamic Island.

3. **Obtain the matched button’s tap coordinates without reading the tree.**

   `WDAClient` exposes neither an element rectangle helper nor an element tap helper. Use its existing session request method locally:

   ```python
   rect = c._session_request("GET", f"/element/{eid}/rect")
   ```

   Validate the rectangle and call the injected `tap()` at its center. This retains the normal action path and cache invalidation without adding a client method.

4. **Preserve recording state checks and cleanup.**

   In `_toggle_recording()`:

   - Open Control Center and resolve the button/value.
   - Preserve errors for a missing control or an already requested state.
   - Tap its center; retain the existing 3.5-second countdown when starting.
   - Resolve the button again after tapping and read its value afresh.
   - Require `"1"` for recording and `""` for stopped; missing elements or unexpected values fail verification.
   - Close Control Center in `finally`, including lookup, tap, and verification failures.

   Keep the measured Control Center gestures initially. Its `wait_stable()` calls read screenshots, but removing those is separate from eliminating the expensive full-tree reads. Measure the targeted lookup on-device before claiming a specific speedup.

5. **Define a small, explicit take format and API.**

   Add:

   ```python
   dry_run_take(steps, path)
   replay_take(path, record=False)
   ```

   Support three input operations:

   ```python
   [
       {"op": "tap", "label": "Settings", "after": 0.6},
       {"op": "swipe", "direction": "up", "amount": 0.4,
        "seconds": 1.0, "after": 0.5},
       {"op": "wait", "seconds": 1.5},
   ]
   ```

   A saved JSON file should contain a format version, screen dimensions in **points**, and resolved steps: tap `x/y`, swipe endpoints/duration, waits, and fixed post-action delays. Retain labels only as descriptive metadata.

6. **Make the dry run an executed rehearsal that resolves each tap once.**

   Later labels may appear only after earlier actions, so resolve steps sequentially while actually performing them, without recording:

   - Validate the complete input structure before moving the phone.
   - Capture `screen_info()` once for dimensions and swipe calculations.
   - Resolve each tap through `find_text(label, exact=True)`, then save and tap the returned center.
   - Reject missing or ambiguous matches unless an explicit index was provided.
   - Convert each slow swipe into point endpoints once, save them, and execute `swipe(..., seconds=...)`.
   - Execute the supplied waits and post-action delays.

   Write the JSON only after the entire rehearsal succeeds, preferably through a temporary sibling file followed by replacement. A failed rehearsal must not publish a complete-looking take.

7. **Replay only saved coordinates and fixed delays.**

   Load and validate the entire JSON before starting recording or changing WDA settings. Reject unknown versions/operations, nonfinite coordinates, invalid dimensions, and negative timing values.

   The replay loop must call only coordinate `tap()`, coordinate `swipe()`, and `time.sleep()`. It must not call `find_text()`, `tap_text()`, `ui_tree()`, `screen_info()`, or `wait_stable()`.

   Define timing as saved gesture durations plus fixed delays after completed calls. WDA/network latency still affects total elapsed time; this is not a hard real-time scheduler. Document that the caller must restore the same starting screen, orientation, and layout before replay.

8. **Scope zero idle wait to coordinate replay and restore it reliably.**

   Use this lifecycle:

   ```text
   validate take
   optionally start_recording()
   set waitForIdleTimeout to 0
   try:
       execute saved coordinate steps and delays
   finally:
       restore config.WDA_IDLE_WAIT
       optionally stop_recording()
   ```

   Use nested cleanup so stopping a recording is still attempted if restoring settings fails. Track whether this invocation successfully started recording, so it never stops a pre-existing recording.

   For predictable replay timing, abort if setting zero fails. Restore in `finally` after a successful setting change, following `_human_gesture`’s shared-session discipline. Preserve the original replay error if cleanup also fails, while reporting the cleanup failure.

   The recording wrapper necessarily performs its targeted verification reads outside the coordinate loop. Also note that WDA session replacement reapplies default settings: a long pause or session recovery can reset zero idle wait. If supporting such takes, reassert zero before subsequent gestures without adding screen reads.

9. **Verify the behavior with isolated fakes, then an on-device rehearsal.**

   Check that:

   - Recording uses the typed class chain, rectangle endpoint, and fresh post-tap value; no `/source` call occurs.
   - Missing controls, already-on/off states, failed toggles, and exceptions close Control Center.
   - Dry runs resolve each tap once and save the coordinates actually used.
   - Replay succeeds with all perception helpers configured to raise if called.
   - Idle wait restores after success, gesture failure, and interruption.
   - Optional recording cleanup runs after replay failure.
   - Invalid take files fail before any phone action.

   Finally, measure start/stop latency on iOS 27 and replay a short take twice from the same initial screen. Keep validation code transient or within the workspace constraint; leave `src/` untouched.
