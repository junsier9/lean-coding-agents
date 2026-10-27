from collections import namedtuple
from functools import wraps
from inspect import signature
from weakref import WeakKeyDictionary

CacheInfo = namedtuple("CacheInfo", "hits misses size")

def memoized_method(method):
    states = WeakKeyDictionary()
    call_signature = signature(method)
    receiver_name = next(iter(call_signature.parameters))

    def state(owner):
        if owner not in states:
            states[owner] = {"values": {}, "hits": 0, "misses": 0}
        return states[owner]

    @wraps(method)
    def wrapper(*args, **kwargs):
        bound = call_signature.bind(*args, **kwargs)
        bound.apply_defaults()
        owner = bound.arguments[receiver_name]
        current = state(owner)
        key = tuple((name, value) for name, value in bound.arguments.items() if name != receiver_name)
        try:
            hash(key)
        except TypeError:
            cacheable = False
        else:
            cacheable = True
        if cacheable and key in current["values"]:
            current["hits"] += 1
            return current["values"][key]
        current["misses"] += 1
        result = method(*bound.args, **bound.kwargs)
        if cacheable:
            current["values"][key] = result
        return result

    def cache_info(owner):
        current = state(owner)
        return CacheInfo(current["hits"], current["misses"], len(current["values"]))

    def cache_clear(owner):
        states.pop(owner, None)

    wrapper.cache_info = cache_info
    wrapper.cache_clear = cache_clear
    return wrapper
