# Sandbox Execution — engineering notes

Raw working notes. Not a deck plan — deliberately unstructured, so the
crafter has to impose a narrative rather than transcribe one.

## Why sandbox at all

Agents write code and we run it. Untrusted generated code touching the host
filesystem, the network, or long-lived credentials is the obvious hazard. The
less obvious one is blast radius on *our* side: a runaway loop that fills a
disk, or a background process that outlives the request that spawned it.

## The backends we actually use

macOS Seatbelt (sandbox-exec) needs no daemon. Policy is a small S-expression:
deny by default, allow reads broadly, confine writes to two subtrees, allow
outbound network. Cheap to start — no container pull, no image build. macOS
only, and Apple has deprecated the CLI more than once without removing it.

Docker/OpenSandbox gives real isolation and reproducibility, at the cost of a
daemon, an image, and a pull. Cold start is seconds, not milliseconds. Right
answer for multi-tenant, overkill for a laptop.

E2B is a hosted microVM. No local daemon, strong isolation, but every call
crosses the network and you are trusting someone else's control plane.

Local (no sandbox) is honest about what it is. Fine behind an approval gate,
indefensible without one.

## Timeouts are the thing nobody plans for

Per-step wall clock defaults to 30 seconds here. That number is invisible
until some real workload — a document render, a model call chain, a test
suite — runs past it. Then it surfaces as a mysterious truncation rather than
a clear error, and the agent typically misdiagnoses it as "the service is
slow" because that is what it looks like from inside.

Two mitigations. Raise the limit, which is blunt and affects everything. Or
make the underlying work pollable: start, poll in slices, collect. The second
is more work and strictly better, because it also gives you progress
reporting for free.

## Filesystem shape

Per-thread workspace directories keep sessions from colliding. Skills get
copied in rather than symlinked — Path.rglob stopped following directory
symlinks in Python 3.13, so a symlinked skill is discovered on 3.12 and
silently vanishes on an interpreter upgrade. Copy and record a manifest.

Writes outside the workspace are denied. This bites when a service the agent
talks to wants to write into its own installation directory; the fix is to
give that service a state directory of its own.

## Numbers worth remembering

30s default per-step limit. ~200-500ms to create a git worktree if you isolate
that way. 1.3GB for a container image with LibreOffice and fonts baked in.
Roughly 8 concurrent model calls before a typical inference endpoint starts
returning 429s.
