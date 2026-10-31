"""Generic Hermes hook scaffold. No native MCP binding is claimed or installed.

An audited Host loader must explicitly supply a trusted async retrieve callback,
domain selection and disclosure acknowledgment. No memory provider is registered.
"""
from cognivault.recall import RecallCore, RecallPolicy, RecallRequest
from cognivault.recall.hosts import unavailable


def register(ctx, *, retrieve=None, allowed_domains=(), context_delivery_acknowledged=False):
    core = (RecallCore(retrieve, RecallPolicy(allowed_domains=tuple(allowed_domains)))
            if callable(retrieve) and allowed_domains and context_delivery_acknowledged is True else None)

    async def before_llm(*, session_id=None, turn_id=None, user_message=None, **kwargs):
        if core is None:
            return {"context": unavailable("HERMES_NATIVE_BINDING_UNSUPPORTED")}
        if (type(session_id) is not str or not session_id or type(turn_id) is not str or not turn_id
                or type(user_message) not in (str, list)):
            return {"context": unavailable("MISSING_HOST_IDENTITY")}
        text = user_message if isinstance(user_message, str) else "\n".join(
            p["text"] for p in user_message if isinstance(p, dict) and p.get("type") == "text" and type(p.get("text")) is str)
        result = await core.recall(RecallRequest(session_id, turn_id, text))
        return {"context": "" if result.status in {"ALREADY_HANDLED", "NOT_NEEDED"} else result.injection}

    ctx.register_hook("pre_llm_call", before_llm)
