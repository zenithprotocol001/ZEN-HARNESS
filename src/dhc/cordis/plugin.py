"""@plugin decorator: wires an apply function into a Context, returning disposables."""

from functools import wraps

from dhc.cordis.context import Context


def plugin(name: str):
    def decorator(apply_fn):
        @wraps(apply_fn)
        async def wrapper(ctx: Context, config: dict | None = None):
            config = config or {}
            disposables = await apply_fn(ctx, config)
            if disposables is None:
                return None
            if isinstance(disposables, list):
                for d in disposables:
                    if d is not None:
                        ctx.add_disposable(d)
            else:
                ctx.add_disposable(disposables)
            # Return the disposables so external callers (e.g. the
            # plugin loader) can call them directly on unload without
            # scanning the context's private disposable stack.
            return disposables

        wrapper._plugin_name = name
        return wrapper

    return decorator
