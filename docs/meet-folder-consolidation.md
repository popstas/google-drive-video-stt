# Consolidating the Meet roots Meet has abandoned

[docs/meet-recordings-folder.md](meet-recordings-folder.md) records *why* an employee
ends up with two `Google Meet` roots: sharing one makes Meet abandon it and open
another beside it. This document is about what to do afterwards -- how to move the
stranded calls into the live root, what that changes for access, and what it costs the
next cycle.

Everything below was measured on a ten-employee deployment in September 2026, by
planning the move read-only, running it, and reading the result back.

## Why the calls cannot simply be left there

The abandoned root keeps its contents and stays readable, so nothing looks lost. It is
lost to the pipeline all the same:

- `walk` reads one folder per employee, the live root. Anything in an abandoned root is
  outside every listing it will ever make.
- `meet` finds a recording through the Meet API and the file it names, so the folder
  does not matter -- but discovery only looks back as far as its window
  (`meet.first_look_hours`, then the mark). Calls older than that are out of reach on
  that path too.

So the history splits in two, and one half is unreachable by any cycle. Moving the
folders is what rejoins it.

## Moving is safe; only sharing is not

**Observed.** Creating folders, writing files and moving folders have never made Meet
change roots -- only sharing the root has. The abandoned root is in any case already
abandoned, so nothing further can be lost by emptying it.

## What the move changes: access

This is the part that surprises, and the reason a move is worth planning rather than
scripting blind.

**A meeting folder inside a shared root is readable by everyone that root is shared
with, through inheritance.** Moving it into an unshared root cuts that path.
*Measured* on the first folder moved: permissions on the folder, and on a file inside
it, returned nothing beyond the owner, where before the move the root's two writers
reached both.

**Permissions granted on the file itself survive the move**, because they are not
inheritance. *Measured* after the move: 81 shared items across nine live roots, 72 of
them inside folders that had just been moved.

Those 81 come in two shapes, and the difference matters:

- **66 name accounts** -- reader on the recording, writer on the transcript document --
  and the accounts named are the other participants of that call. That is the shape
  Meet produces for participants by itself. Reading it as deliberate sharing by the
  employee would be a mistake.
- **15 carry `type=anyone`**: link sharing, not discoverable by search, on recordings
  and transcripts of client calls, all belonging to one employee. The move did not
  create this and does not affect it. It only made it visible.

**Audit files, not folders.** An earlier audit of the same deployment listed
permissions on the *subfolders* and concluded that almost no individual sharing
existed. It was wrong by 81 items: inheritance is what a folder reports, and every
explicit grant sits on the file inside it.

## The procedure

Plan first, read-only, and write the plan out with ids. Execute from that file rather
than re-deriving anything, and handle each employee under their own delegated
credentials -- a folder can only be moved by an account that owns it.

Re-check these immediately before each write, because a plan goes stale the moment a
new recording lands:

- the folder is owned by this employee;
- its current parent is still the abandoned root the plan names;
- the target is this employee's own top-level `Google Meet` root, and carries no
  permissions beyond the owner.

**Verify on one folder before doing the rest.** Move it, then read the permissions on
it and on a file inside. The inherited grants should be gone. If they are still there,
stop: the move is not doing what it was for.

**Log every write** -- employee, folder, source, target -- as it happens. Reversing a
move is the same call with the parents swapped, and without a log a partially finished
run cannot be undone by hand.

## Name collisions: recurring meetings

A recurring meeting keeps its folder name, so the live root often already holds a
folder of the same name. Moving the old one in would leave two identical names side by
side. Move the *files* into the existing folder instead, and leave the emptied folder
where it stands. *Measured*: 3 of 135 folders, 12 files.

## What it costs the next cycle

Everything moved in is a recording with no artifacts beside it, and "done or not" is
derived from exactly those artifacts. To the pipeline the moved history is a backlog
that just appeared.

*Measured* after moving 135 folders: a `walk` dry run found 108 recordings pending
where `meet` found 22.

**`run.since` is the guard, and it is needed even when discovery is `meet`:**

- `walk` lists the whole folder, so with no cutoff the entire moved history is in
  scope;
- `meet` is bounded by its look-back, but `meet.fallback: walk` means an employee the
  Meet API refuses is walked instead -- and that one employee's whole history then
  enters the queue;
- `auto` with no cursor sweeps as well.

Set `run.since` to the date the deployment should start caring about *before* moving
anything. A cutoff set afterwards still works -- the backlog is counted, not processed
-- but only if the cycle has not run in between.

## What consolidating does not fix

- **An employee whose only root is shared has nowhere to move to.** Un-sharing does not
  bring Meet back to it: the next recording opens a new root, and only then is there a
  target. *Measured*: one of the ten.
- **Shortcuts move with their folder and stay shortcuts.** They point at a recording in
  the organiser's Drive, neither discovery path follows one, and moving changes nothing
  about whether those calls are processed.
- **Explicit file grants and link shares survive**, as above. Revoking them is a
  separate decision, and a write to that employee's Drive.

## Checking the result

- `gdstt doctor --drive`: subfolder and recording counts per employee, and a warning on
  any root that carries a permission beyond its owner.
- `gdstt run-once --mode meet --dry-run` against `--mode walk --dry-run`: the gap
  between the two is the backlog the move made visible.
- `files.list` with `permissions` requested inline: one request per meeting folder
  rather than one per file, and it reports every explicit grant that the folder-level
  view hides.
