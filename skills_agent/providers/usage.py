"""Gateway-reported context usage, without local token estimates."""


def usage_snapshot(model, usage, *, total_only=False):
    if not isinstance(usage, dict):
        return None
    if total_only:
        tokens = usage.get("total_tokens")
        if type(tokens) is not int or tokens < 0:
            return None
        return {"version": 2, "model": model, "tokens": tokens}
    counts = [usage.get("input_tokens"), usage.get("output_tokens")]
    if any(type(count) is not int or count < 0 for count in counts):
        return None
    return {"version": 2, "model": model, "tokens": sum(counts)}


def count_from_usage(model, messages) -> int | None:
    """New messages never change the most recent reported context length."""
    for message in reversed(messages):
        snapshot = message.get("_token_usage") if message.get("role") == "assistant" else None
        if not isinstance(snapshot, dict) or snapshot.get("version") != 2:
            continue
        if snapshot.get("model") != model:
            return None
        tokens = snapshot.get("tokens")
        if type(tokens) is int and tokens >= 0:
            return tokens
    return None
