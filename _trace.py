"""Trace Copilot CLI JSONL events — run: copilot --output-format json ... | python3 _trace.py"""
import sys, json

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        obj = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        continue

    t = obj.get("type", "")
    d = obj.get("data", {})

    if t == "tool.execution_start":
        args = d.get("arguments", {})
        print(f"START: {d.get('toolName')} -> {json.dumps(args)[:200]}")
    elif t == "tool.execution_complete":
        r = d.get("result", {})
        content = r.get("content", "")
        print(f"DONE: success={d.get('success')} len={len(content)}")
        if content:
            print(f"  OUT: {content[:300]}")
    elif t == "assistant.message_delta":
        print(f"DELTA: {repr(d.get('deltaContent', '')[:120])}")
    elif t == "assistant.message":
        c = d.get("content", "")
        tr = d.get("toolRequests", [])
        print(f"MSG: content={repr(c[:120])} tools={len(tr)}")
    elif t == "assistant.turn_end":
        print(f"TURN_END: {d}")
