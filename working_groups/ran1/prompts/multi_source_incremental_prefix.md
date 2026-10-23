You produce a unified session list for a 3GPP RAN1 time-slot.

This is an INCREMENTAL UPDATE. You receive:
1. A "Previous merge result" (BASELINE) — the session list made from the
   previous version of the same sources.
2. EVERY cell of every source for this time slot, in the same sections as a
   full merge ("Main Schedule", "Vice-chair detail"). Each cell is tagged:
   - UNCHANGED: the same text as when the baseline was made.
   - CHANGED: the text is different now. The text before follows "was:".
   - NEW: a cell that was not there before.
3. "Removed cells" (only when there are some): cells that were there before
   and are gone now, with their old text, tagged REMOVED.

Your job: apply ONLY what changed.

## Areas

An AREA is the room (or the rooms of a multi-room cell) that a cell covers.
A Main Schedule cell covers the rooms in its label. A vice-chair cell covers
the room its CONTENT matches (its label is unreliable, as in a full merge).

## What to rebuild and what to keep

- Rebuild an area ONLY when a CHANGED, NEW or REMOVED cell covers it.
- Rebuild it from ALL current cells for that area — changed and unchanged
  alike, from every source — with the full-merge rules below: the Main
  Schedule decides what is in the room, vice-chair cells add detail. The
  rebuilt area must look exactly as a full merge of the current cells would
  make it. Ignore the baseline's sessions for that area.
- For every other area, COPY THE BASELINE sessions verbatim (same name,
  duration, chair, group_header, agenda_item, specified_start_time,
  room_name).
- An UNCHANGED cell never changes the baseline by itself, even when it
  disagrees with the baseline. Vice-chair documents often contain old copies
  of Main Schedule tables; such a copy keeps saying what it said before, and
  the baseline already accounts for it.
- A REMOVED cell's area is rebuilt from the remaining current cells for that
  area. If no cell covers the area any more, drop its sessions.

## Output

The complete session list for the time slot: the kept baseline sessions and
the rebuilt areas together.

# Full-merge rules (used to rebuild an area)

