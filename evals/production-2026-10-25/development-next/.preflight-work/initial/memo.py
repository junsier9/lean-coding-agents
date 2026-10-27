from collections import namedtuple
from functools import wraps
from weakref import WeakKeyDictionary

CacheInfo = namedtuple("CacheInfo", "hits misses size")

def memoized_method(method):
    states = WeakKeyDictionary()

    def state(owner):
        if owner not in states:
            states[owner] = {"values": {}, "hits": 0, "misses": 0}
        return states[owner]

    @wraps(method)
    def wrapper(owner, *args, **kwargs):
        current = state(owner)
        key = (args, tuple(sorted(kwargs.items())))
        if key in current["values"]:
            current["hits"] += 1
            return current["values"][key]
        current["misses"] += 1
        result = method(owner, *args, **kwargs)
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
