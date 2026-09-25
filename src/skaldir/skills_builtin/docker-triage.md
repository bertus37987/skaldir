---
name: docker-triage
description: "Triage a container that was supposed to be running: verify daemon, image, ports, and logs — in that order — and report the first real blocker. Use when a dockerized service is 'down'."
version: 0.1.0
---

# Docker triage

## Steps

1. Is the daemon reachable? Run `docker info` (or the project's `docker_ensure` equivalent) with a short timeout; record exact error if not.
2. Does the image exist locally? `docker images | grep <image>`; if not, does the compose file pin a pullable tag?
3. Was the container started and is it up? `docker ps -a | grep <name>`; note restart count and exit code.
4. Read the last 50 log lines (`docker logs --tail 50 <name>`) and quote the traceback/panic verbatim.
5. Ports: `docker port <name>` vs what the client expects.
6. Report: the FIRST failing step above is the blocker; everything downstream is marked "not reached" — do not speculate past the evidence.

## Acceptance criteria

- Each step's actual output (or error) is quoted.
- Exactly one blocker named; downstream unchecked steps labeled, not guessed.
- Fix suggestion only for the blocker step.
