#!/bin/bash
# Claude Code SessionStart hook installed by Prompt Rack's BG button: records this session's Terminal tty and claude pid
# as ~/.prompt-rack/tty/<session-uuid> = "ttysNNN PID". auto-bg.py reads it to know which tab gets the ctrl+x chord.
U=$(python3 -c 'import json,sys;print(json.load(sys.stdin)["transcript_path"].rsplit("/",1)[1][:-6])')
mkdir -p "$HOME/.prompt-rack/tty"
echo "$(ps -o tty= -p $PPID | tr -d ' ') $PPID" > "$HOME/.prompt-rack/tty/$U"
