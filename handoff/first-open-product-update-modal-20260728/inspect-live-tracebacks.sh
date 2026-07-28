#!/usr/bin/env bash
set -Eeuo pipefail

docker logs \
  --since 2026-07-28T06:33:58Z \
  --until 2026-07-28T06:38:00Z \
  open-webui 2>&1 |
  awk '
    /Traceback \(most recent call last\)/ {
      showing = 1
      remaining = 160
      block++
      print "TRACEBACK_BLOCK=" block
    }
    showing {
      print
      remaining--
      if (remaining <= 0) {
        showing = 0
      }
    }
  ' |
  sed -E 's/(Authorization: Bearer )[A-Za-z0-9._-]+/\1[REDACTED]/g'

printf '\nEVENT_COUNTS\n'
docker logs \
  --since 2026-07-28T06:33:58Z \
  --until 2026-07-28T06:38:00Z \
  open-webui 2>&1 |
  awk '
    /Traceback \(most recent call last\)/ { tracebacks++ }
    /Started server process/ { started++ }
    /Application startup complete/ { ready++ }
    /Child process \[[0-9]+\] died/ { died++ }
    /remote origin is not allowed/ { blocked_origin++ }
    END {
      printf "traceback_markers=%d\n", tracebacks
      printf "server_process_started=%d\n", started
      printf "application_startup_complete=%d\n", ready
      printf "child_process_died=%d\n", died
      printf "blocked_remote_origin_markers=%d\n", blocked_origin
    }
  '
