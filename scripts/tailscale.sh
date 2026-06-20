#!/bin/bash
# Tailscale wrapper — automatically uses the correct socket path
/opt/homebrew/bin/tailscale --socket=/Users/guan/Library/Caches/tailscale/tailscaled.sock "$@"
