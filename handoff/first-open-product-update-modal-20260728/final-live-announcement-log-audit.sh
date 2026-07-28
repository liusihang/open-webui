#!/usr/bin/env bash
set -Eeuo pipefail

since=2026-07-28T06:58:00Z
until=$(date -u +%Y-%m-%dT%H:%M:%SZ)

docker logs --since "$since" --until "$until" open-webui 2>&1 |
  awk -v since="$since" -v until="$until" '
    /Traceback \(most recent call last\)/ { tracebacks++ }
    /Child process \[[0-9]+\] died/ { child_deaths++ }
    /Started server process/ { server_starts++ }
    /ReadTimeout/ { read_timeouts++ }
    /runtime_finalization/ { runtime_finalization++ }
    / \| ERROR +\| / {
      errors++
      line=$0
      sub(/ - .*/, "", line)
      error_sources[line]++
    }
    /HTTP\/1\.[01]" 5[0-9][0-9]/ { http_5xx++ }
    END {
      printf "window_since=%s\nwindow_until=%s\n", since, until
      printf "traceback_markers=%d\n", tracebacks
      printf "child_process_died=%d\n", child_deaths
      printf "server_process_started=%d\n", server_starts
      printf "read_timeout_markers=%d\n", read_timeouts
      printf "runtime_finalization_markers=%d\n", runtime_finalization
      printf "http_5xx=%d\n", http_5xx
      printf "error_lines=%d\n", errors
      for (source in error_sources) {
        printf "error_source count=%d source=%s\n", error_sources[source], source
      }
    }
  '
