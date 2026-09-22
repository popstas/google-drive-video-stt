# Reading each employee's own Drive through domain-wide delegation

## Overview

- A watched folder is configured by **e-mail alone**. The service impersonates that
  employee, finds the folder Google Meet is writing into right now, and reads it as
  its owner.
- This removes the two things that broke the deployment: a folder id that silently
  stops being the live one, and sharing -- which is what makes Meet abandon a root in
  the first place (`docs/meet-recordings-folder.md`).
- Everything that exists keeps working. No service account configured means today's
  behaviour, unchanged: a user OAuth token and `folder_id` per entry.

## Context (from discovery)

- Measured on a real domain, read-only: impersonation reaches every employee's own
  `Google Meet` root; recordings, Meet transcripts and shortcut targets all read.
  Without impersonation the same folders answer `notFound`.
- The admin authorized one scope for the service account's client id:
  `https://www.googleapis.com/auth/drive`. `drive.readonly` was not authorized, so a
  read-only mode cannot be offered until it is.
- An account may hold more than one folder named `Google Meet` at the top of My
  Drive: the live one, plus every root abandoned after a share. Legacy layouts are
  named `Meet Recordings` or `Meet <n> - <person>` and hold no meetings.
- Decision from the operator: the fleet will be consolidated into one folder per
  person, and staff are told not to share the root. **Watching several roots at once
  is therefore out of scope.** The service resolves the live root and warns about the
  rest.
- The pipeline downstream is keyed on `folder_id`: `Config.folder_by_id`, `since_for`,
  `folder_telegram_chat`, the booking gate, `find_configured_ancestor`, the completion
  webhook's `file.folder_id`. None of it should learn about e-mails.

## Development approach

- Testing approach: test-first for the resolution rules, regular for the wiring.
- Every task carries its own tests; the suite must pass before the next task starts.
- Backwards compatibility is a hard requirement: with no service account in the
  config, every existing test must pass untouched.
- Test data uses `@example.com` addresses and invented names only.
- Tests need Linux (`fcntl`); run them in the project's Docker image.

## Implementation steps

### Task 1: a service account in the config, and folders that carry only an e-mail

- `google.service_account` (inline mapping) and `google.service_account_file` (path),
  resolved exactly like the existing `credentials` / `credentials_file` pair: inline
  wins, both set is an error, a relative path resolves against the config file.
- `EmployeeFolder.folder_id` becomes optional **only when** a service account is
  configured and the entry carries an `email`. Without a service account, a missing
  `folder_id` keeps raising today's error.
- `folders[].name` becomes optional too; Task 3 fills it in.
- New `meet.folder_names`, default `["Google Meet"]`. Legacy names are deliberately
  absent: those folders hold no meetings, and matching them would only add candidates
  that confuse the resolver.
- Tests: an e-mail-only entry is rejected without a service account and accepted with
  one; inline and file sources conflict the way the OAuth ones do; a repeated e-mail
  is rejected the way a repeated folder id is.

### Task 2: delegated credentials

- `auth.build_drive_service(config=..., subject=...)`: with a service account
  configured, build credentials from it and `with_subject(subject)`; without one, or
  without a subject, behave exactly as now.
- Translate the two failures an operator will actually hit:
  - `unauthorized_client` -> "domain-wide delegation is not authorized for client id
    <id> and scope <scope>; add it in Admin Console -> API controls";
  - `invalid_grant` -> "<address> is not a user in this domain".
- Never log the key. The service account's `client_email` and `client_id` may be
  logged: they are exactly what an admin needs to authorize it.
- Tests: the subject reaches the credentials; both errors are translated; no service
  account means the old path is taken.

### Task 3: resolving the live Meet root

A new module, `src/meet_root.py`, that knows nothing about the rest of the pipeline:

- `resolve(service)`: folders owned by the impersonated user, whose parent is their My
  Drive root (`files.get(fileId="root")`), whose name is in `meet.folder_names`, not
  trashed.
  - exactly one -> that is the folder;
  - several -> the newest `createdTime` wins and the count is reported: the newest is
    the live root, the others were abandoned after a share;
  - none -> reported as "no Meet folder", which the caller turns into a skip.
- `owner_name(service)`: `about.get(fields="user")` under impersonation, used to fill
  an empty `folders[].name`. The name matters: `speaker_roles` decides which speaker
  is the manager from it.
- Tests: one root; several roots pick the newest and report the count; none at all; a
  folder of the right name the user does not own is ignored; one nested below My Drive
  root is ignored; the owner name is read.

### Task 4: an effective config, resolved once per cycle

- At the start of a cycle, and of every command that walks folders, each delegated
  entry is resolved: impersonate, find the root, `replace(entry, folder_id=<resolved>,
  name=<name or owner name>)`.
- The rest of the pipeline receives a `Config` whose entries all carry ids, exactly as
  today. Nothing downstream learns about delegation.
- The resolution is redone every cycle. That is what makes the absence of a multi-root
  fallback affordable: when Meet moves, the next cycle follows it.
- **Accepted risk, stated rather than discovered later:** a recording that lands in
  the old root immediately before a move, while Drive is still processing it, is never
  picked up -- the next cycle is already looking at the new root. The `doctor` report
  on abandoned roots is the compensating control.
- A Drive service per folder is built alongside, so every folder is read as its owner.
- Tests: an e-mail-only entry becomes an entry with an id; a missing root skips that
  employee and leaves the others alone; the name is filled from Drive only when the
  config leaves it empty.

### Task 5: one failing employee must not stop the cycle

- `_discover_by_walk` re-raises `RefreshError` / `AuthError` today, which was right
  with one shared token: no token, no cycle. Per employee it is wrong -- one bad
  address would abort everybody.
- Delegated failures, in resolution and in listing alike, count as `folder_errors`,
  notify, and the cycle carries on with the rest.
- Tests: one employee failing to impersonate leaves the others processed, with the
  cycle's `folder_errors` at one.

### Task 6: the changes feed is not used in delegated mode

- The cursor is a position in **one account's** journal; under delegation there is no
  such account. Until a cursor per subject exists, delegated mode walks.
- `run.discovery: auto` together with a service account is a config error naming this
  follow-up, rather than a silent fallback.
- `gdstt cursor show` says the cursor is unused in delegated mode.
- Cost check: walking is one listing per folder plus one per meeting subfolder, per
  cycle. At the current fleet size -- ten employees, about 150 meetings -- that is
  roughly 160 requests every ten minutes, far inside Drive's quota.
- Tests: `auto` plus a service account is rejected; a delegated cycle takes no cursor.

### Task 7: commands that start from a file id

`process`, `reprocess`, `latest`, `speakers set` and `changes` are given an id, not a
folder. In delegated mode there is no single account to ask.

- Resolve by trying each configured subject's `files.get` until one succeeds; the
  first hit decides whose Drive the file is in, and that subject's service does the
  work.
- At most one request per employee on a manual command, which is acceptable.
- Tests: a file in the second employee's Drive is found and processed with that
  employee's service; a file nobody can see reports "not visible to any configured
  employee" rather than a traceback.

### Task 8: `doctor --drive` says whether the fleet is healthy

Per employee, in delegated mode:

- impersonation: ok, or the translated failure;
- the resolved root: name, id, and how many candidates were seen -- more than one
  means roots abandoned after a share;
- counts: meeting subfolders, recordings, shortcuts, and shortcut targets that do not
  open;
- **permissions on the root**: anything beyond the owner is a warning, because that
  folder is one recording away from being abandoned.

The permission warning is a report and nothing more. Removing a share automatically is
out of scope: it undoes something a person did deliberately, and the operator has not
asked for it.

Tests: the report lists every employee; a root with an extra permission produces the
warning; several candidates produce the "abandoned roots" line.

### Task 9: live verification

In this order, on the real domain:

1. `doctor --drive` with delegation: every employee resolves, the counts look sane.
2. `run-once --dry-run` for one employee: the pending list matches what `doctor` saw.
3. `run-once` for that one employee with STT on: artifacts land inside the meeting
   subfolder, owned by the employee, and nothing is written into the root.
4. Re-run: nothing pending.
5. Only then the rest of the fleet.

## Documentation

- README: a delegation section -- what the admin authorizes, what the config looks
  like with an e-mail only, and what `doctor --drive` prints.
- AGENTS.md: the effective-config rule (delegation is resolved before the pipeline, so
  nothing downstream knows about e-mails) and the per-employee error isolation.
- `docs/meet-recordings-folder.md`: keep the measurements, correct the rule to match
  this plan -- resolve the live root each cycle and warn about the rest, rather than
  watch every root.

## Follow-ups, deliberately not in this plan

- **A changes feed per employee.** One cursor per subject, kept beside the current
  one. Worth doing when the fleet grows enough for walking to hurt; today it does not.
- **Granting a manager access per meeting.** The service, acting as the owner, adds a
  reader to each processed meeting subfolder -- the only sharing measured not to
  disturb Meet. Needs a decision about who, and about what happens when that person
  changes.
- **Removing a stray share automatically.** Mechanically easy once Task 8's warning
  exists; a policy decision rather than a technical one.
- **A read-only mode.** Possible as soon as `drive.readonly` is authorized alongside
  `drive`; artifacts would then have to be written somewhere other than the employee's
  Drive.
