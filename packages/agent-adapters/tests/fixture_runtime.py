#!/usr/bin/env python3
"""Executable provider protocol fixture; never contacts a model."""

import json
import os
from pathlib import Path
import signal
import sys
import time

SESSION = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
if sys.argv[1:] == ["login", "status"]:
    print("Logged in using ChatGPT", file=sys.stderr)
    sys.exit()
if sys.argv[1:] == ["auth", "status"]:
    print(json.dumps({"loggedIn": True, "authMethod": "claude.ai", "email": "private@example.invalid", "token": "SECRET_AUTH_VALUE"}))
    sys.exit()
if "--version" in sys.argv:
    print("fixture 1.2.3")
    sys.exit()
if "--help" in sys.argv:
    print("--json --sandbox --ignore-user-config --ignore-rules --output-format --restricted --permission-prompts --safe-mode --resume --tools SESSION_ID")
    sys.exit()

text = sys.stdin.read()
claude = "--print" in sys.argv
readiness_task = text.startswith("Read only fixture.txt") or text.startswith("Return the exact token from your previous reply")

def emit(record):
    print(json.dumps(record), flush=True)

emit({"type": "system", "subtype": "init", "session_id": SESSION} if claude else {"type": "thread.started", "thread_id": SESSION})
if text == "[hang]" or "then wait sixty seconds" in text:
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    while True:
        time.sleep(0.1)
if text == "[flood]":
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    while True:
        emit({"type": "item.completed", "item": {"type": "agent_message", "text": "x" * 4000}})
if text == "[death]":
    os._exit(8)
if text == "[malformed]":
    print("not json", flush=True)
    sys.exit()
if text == "[huge]":
    print("x" * 70000, flush=True)
    sys.exit()
if text == "[stderr]":
    sys.stderr.write("SENSITIVE STDERR " * 100)
if text == "[bad-shape]":
    emit({"type": "assistant", "message": {"content": ["wrong"]}} if claude else {"type": "item.completed", "item": "wrong"})
if text == "[session-change]":
    emit({"type": "system", "subtype": "init", "session_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"} if claude else {"type": "thread.started", "thread_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"})
if text == "[reasoning]":
    if claude:
        emit({"type": "assistant", "message": {"content": [{"type": "thinking", "thinking": "SECRET REASONING"}]}})
    else:
        emit({"type": "item.completed", "item": {"type": "reasoning", "text": "SECRET REASONING"}})
reply = Path("fixture.txt").read_text().strip() if readiness_task else json.dumps({"instruction": text, "argv": sys.argv[1:]})
if claude:
    emit({"type": "assistant", "message": {"content": [{"type": "text", "text": reply}]}})
    emit({"type": "result", "subtype": "success", "is_error": False, "result": reply,
          "session_id": SESSION, "permission_denials": [{"tool_name": "Bash"}] if text == "[permission]" else []})
else:
    emit({"type": "item.completed", "item": {"type": "agent_message", "text": reply}})
    emit({"type": "turn.failed", "error": {"message": "SECRET"}} if text == "[fail]" else {"type": "turn.completed", "usage": {}})
if text == "[exit-after-success]":
    sys.exit(7)
