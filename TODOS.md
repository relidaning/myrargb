# Toso-list

This file record the bugs/todos I found during processing and I wanna you do this later.

- [ ] the movie year should be update before the record insertation instead of inside predicting.
- [ ] **Handler crash kills consumer silently** — `handler.py` lacked try/except around callbacks. Added try/except in handler.py (2026-05-15), but consumers still get stuck after processing some items. Root cause not fully identified — need deeper investigation of what's killing or stalling the daemon threads.
- [ ] **Predict consumer stalls after ~150 items** — after restart, predict lag dropped from 2155 to 2002 then froze. Consumer stops receiving/polling messages without logging errors. Same pattern as before the handler fix.
- [ ] **IMDb consumer stalls at ~2500 lag** — 5 IMDb consumer threads processing but lag not decreasing. Many items fail with `NoneType` errors or 15s timeouts. Error items get committed, but overall throughput appears stuck. Check if all 5 threads are alive.
- [] The logging, should add the function infos, after file name and before the line number.
- [] Add year column in the index.html.
