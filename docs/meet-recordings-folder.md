# How Google Meet chooses the folder it records into

Everything below was measured against real Drive accounts in September 2026, either by
a deliberate experiment on a test account or by reading the state of an existing
deployment. Each claim says which. No part of it is quoted from Google's
documentation, which does not describe this behaviour.

## What Meet writes, and where

A recorded call produces, in the **organiser's** My Drive:

- a root folder named `Google Meet`, at the top level of My Drive;
- inside it, one subfolder per meeting, named after the calendar title or the room
  code, holding the `.mp4` recording and Meet's own transcript as a native Google Doc.

Participants who did not organise the call get a meeting subfolder too, but it holds
**shortcuts** to the organiser's files rather than copies. Opening a shortcut's target
depends on the organiser's sharing, not on the folder the shortcut sits in.

The older flat `Meet Recordings` layout still exists on accounts that predate the
change, usually empty.

## Sharing the root makes Meet abandon it

**Tested.** On a dedicated test account, in one sitting:

1. Several calls were recorded. Each landed in the same `Google Meet` root.
2. One **meeting subfolder** was shared with another account. Three further recordings
   still landed in that same root. Sharing a subfolder changes nothing.
3. The **root** was then shared with another account. The next recording did not go
   there. Meet created a **second root**, also named `Google Meet`, also at the top
   level of My Drive, and used it instead.
4. The sharing was removed from the first root, returning it to private, and two more
   calls were recorded. Both went to the **new** root. Meet does not come back.

So the rule is: a recordings root that is shared with anyone stops receiving
recordings, permanently, from the next recording onwards.

What does **not** trigger it, observed rather than tested in isolation: creating
folders inside the root, and writing files into the root or into a meeting subfolder.
The service writes its own artifacts next to every recording it processes and no move
has ever followed.

## The abandoned folder looks perfectly healthy

**Tested.** The old root keeps its id, its name, its contents and its parent. It is
not trashed. Its id still resolves, still lists, still reports no errors.

Two identically named `Google Meet` folders then sit side by side in My Drive, one
live and one dead, and nothing in the Drive UI distinguishes them. For this service
the failure is silent in exactly the same way: the configured id resolves, every cycle
reports success, and no new recording is ever found. It is the same symptom as a
folder that was renamed and moved while keeping its id, from a different cause.

## What an existing deployment looked like

**Observed.** Ten watched folders, read as their owners:

- every root that had stopped receiving recordings was shared with other accounts;
- every root that was receiving recordings was private to its owner;
- nine of the ten accounts had two roots by then, the second created between one and
  three weeks after the sharing;
- the tenth still had one root, still shared, and no recording since the sharing --
  the move had simply not been triggered yet.

The split is not repairable by un-sharing. The old meetings stay where they are;
moving them into the live root is possible if someone wants one folder again, since
moving and writing do not trigger anything.
[docs/meet-folder-consolidation.md](meet-folder-consolidation.md) is how that move is
planned and run, what it does to inherited access, and what the rejoined history costs
the next cycle.

## Rules this imposes

**Never share a Meet recordings root.** Not with a manager, not with a service
account, not with an admin. The cost is the folder, permanently, plus a history split
across two places.

**Share individual meeting subfolders instead.** That is the only sharing proven safe,
and it is what any access-granting automation should use.

**Do not pin a single folder id.** A configured id is a snapshot of where Meet wrote
when the config was written. Discovery should resolve, each cycle, which root the
account is writing into now -- the newest Meet-named folder it owns at the top level of
its My Drive -- and report the others as abandoned rather than read them. Re-resolving
every cycle is what makes a move cost one cycle instead of a support ticket.

**Treat a root with extra permissions as an alarm.** A root that carries any
permission beyond its owner is one recording away from being abandoned. It is worth
reporting the moment it is seen, while the next call has not happened yet.

**Reading as the owner is unaffected.** Whether a folder is shared changes nothing for
credentials that act as the folder's owner, so an account reading its own Drive -- or a
service impersonating it -- sees every root regardless of sharing state.

## How to check the state again

Everything above is visible through read-only Drive calls, as the folder's owner:

- `files.list` for folders named like a Meet root, owned by the user, whose parent is
  the My Drive root: more than one means a move has happened;
- the created time of each root, and the newest meeting subfolder inside it: the live
  root is the one still gaining subfolders;
- `permissions.list` on each root: anything beyond the owner is the warning sign
  above.

## Finding recordings without knowing the folder

Everything above is about which folder Meet writes into, because searching Drive means
starting from a folder. `run.discovery: meet` does not start there: the Meet API names
the conference and the Drive file it produced, and the folder is whatever that file's
parent turns out to be. A root Meet abandoned after being shared therefore costs that
path nothing -- the new root is found by following the file, not by recognising the
folder.

That does not make the rule below harmless. The recordings are still written into a
root somebody may have shared, the walk is still the fallback, and every artifact is
still written beside the video. Do not share a recordings root.
