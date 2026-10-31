"""JSON-lines Core bridge for a native Host; contains no MCP connection code.

The parent owns native registry dispatch and cancellation. stdin/stdout carry
ephemeral user/evidence data, never diagnostic logs or persisted evidence.
"""
import asyncio
import json
import sys
import threading

from . import RecallCore, RecallPolicy, RecallRequest


def _line():
    raw = sys.stdin.buffer.readline(262145)
    if not raw or len(raw) > 262144 or not raw.endswith(b"\n"):
        raise ValueError("INVALID_BRIDGE_MESSAGE")
    return json.loads(raw)


def _send(value):
    print(json.dumps(value, ensure_ascii=True, separators=(",", ":")), flush=True)


async def serve(initial):
    if (type(initial) is not dict or set(initial) != {"request", "allowed_domains", "context_delivery_acknowledged"}
            or initial["context_delivery_acknowledged"] is not True
            or type(initial["allowed_domains"]) is not list or not initial["allowed_domains"]):
        raise ValueError("INVALID_BRIDGE_CONFIGURATION")
    policy = RecallPolicy(allowed_domains=tuple(initial["allowed_domains"]))
    request_data = dict(initial["request"])
    if "recent_user_messages" in request_data:
        if type(request_data["recent_user_messages"]) is not list:
            raise ValueError("INVALID_BRIDGE_REQUEST")
        request_data["recent_user_messages"] = tuple(request_data["recent_user_messages"])
    request = RecallRequest(**request_data)
    replies = asyncio.Queue()
    loop = asyncio.get_running_loop()
    # A daemon reader cannot hold Python shutdown after Core cancellation.
    # Parent kills this child on Host abort/deadline; no Gateway is owned here.
    def reader():
        try:
            while True:
                value = _line()
                loop.call_soon_threadsafe(replies.put_nowait, value)
        except Exception:
            if not loop.is_closed():
                loop.call_soon_threadsafe(replies.put_nowait, None)
    threading.Thread(target=reader, daemon=True).start()
    sequence = 0

    async def retrieve(queries, limit):
        nonlocal sequence
        sequence += 1
        _send({"type": "retrieve", "id": sequence, "tool": "retrieve_evidence", "arguments": {"queries": queries, "limit": limit}})
        reply = await replies.get()
        if not isinstance(reply, dict) or set(reply) != {"id", "result"} or reply["id"] != sequence:
            raise ValueError("INVALID_BRIDGE_RESPONSE")
        return reply["result"]

    result = await RecallCore(retrieve, policy).recall(request)
    _send({"type": "result", "status": result.status, "delivery_status": result.delivery_status,
           "injection": "" if result.status in {"ALREADY_HANDLED", "NOT_NEEDED"} else result.injection})


def main():
    try:
        asyncio.run(serve(_line()))
    except Exception:
        _send({"type": "error", "error_code": "BRIDGE_UNAVAILABLE"})
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
