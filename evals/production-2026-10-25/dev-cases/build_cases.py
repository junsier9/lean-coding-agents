"""Self-contained Python maintenance fixtures; this file never calls a model."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap

HERE = Path(__file__).resolve().parent
CASES = []


def code(value):
    return textwrap.dedent(value).lstrip("\n")


def case(identifier, family, task, files, reference_files, tests, checks):
    CASES.append({
        "id": identifier, "family": family, "task": task,
        "files": {name: code(value) for name, value in files.items()},
        "reference_files": {name: code(value) for name, value in reference_files.items()},
        "hidden_tests": {"test_contract.py": code(tests)},
        "critical": True, "checks": checks,
    })


case(
    "d01", "validated_immutable_pipeline",
    "账单批次摘要目前会为同一批数据重复写入 validated 审计事件。请消除内部重复校验，"
    "保持 summarize 的公开行为：只接受 list，元素必须严格为 int（bool 不合法），"
    "空批次返回 count=0、total=0；不修改调用者输入。每个有效请求恰好写一次"
    " audit('validated', 元素数)，非法请求不得写成功审计。审计回调异常应原样传播。"
    "验证结束后使用已拥有的不可变快照计算，即使审计回调改动原 list，摘要仍以验证时的值为准。",
    {
        "boundary.py": '''
            class PayloadError(ValueError):
                pass


            def validated_amounts(values, audit):
                # This helper also serves existing internal tuple producers.
                owned = tuple(values)
                if any(type(value) is not int for value in owned):
                    raise PayloadError("amounts must be integers")
                if audit is not None:
                    audit("validated", len(owned))
                return owned


            def accept_request(payload, audit=None):
                if not isinstance(payload, list):
                    raise PayloadError("expected list")
                return validated_amounts(payload, audit)
        ''',
        "app.py": '''
            from boundary import accept_request, validated_amounts


            def _sum_owned(amounts, audit):
                # At present summarize is the only caller.
                amounts = validated_amounts(amounts, audit)
                return sum(amounts)


            def summarize(payload, audit=None):
                """Return a count and signed total in integer cents.

                Refunds are represented by negative values. Request validation
                happens before downstream arithmetic and produces a tuple.
                The external list remains owned by the caller.
                """
                amounts = accept_request(payload, audit)
                return {"count": len(amounts), "total": _sum_owned(amounts, audit)}
        ''',
    },
    {
        "app.py": '''
            from boundary import accept_request


            def _sum_owned(amounts):
                return sum(amounts)


            def summarize(payload, audit=None):
                """Summarize the owned, validated request snapshot."""
                amounts = accept_request(payload, audit)
                return {"count": len(amounts), "total": _sum_owned(amounts)}
        ''',
    },
    '''
        import unittest
        from app import summarize
        from boundary import PayloadError


        class Contract(unittest.TestCase):
            def test_signed_total(self):
                self.assertEqual(summarize([120, -20, 5]), {"count": 3, "total": 105})

            def test_empty(self):
                self.assertEqual(summarize([]), {"count": 0, "total": 0})

            def test_audit_once(self):
                events = []
                summarize([1, 2, 3], lambda *event: events.append(event))
                self.assertEqual(events, [("validated", 3)])

            def test_empty_audit_once(self):
                events = []
                summarize([], lambda *event: events.append(event))
                self.assertEqual(events, [("validated", 0)])

            def test_non_lists(self):
                for value in (None, 4, (1, 2), "12", iter([1, 2])):
                    with self.subTest(value=repr(value)), self.assertRaises(PayloadError):
                        summarize(value)

            def test_non_integer_elements(self):
                for value in (True, 1.5, "1", None):
                    with self.subTest(value=value), self.assertRaises(PayloadError):
                        summarize([1, value])

            def test_invalid_has_no_success_audit(self):
                events = []
                with self.assertRaises(PayloadError):
                    summarize([1, False], lambda *event: events.append(event))
                self.assertEqual(events, [])

            def test_does_not_mutate_input(self):
                values = [5, -1]
                summarize(values)
                self.assertEqual(values, [5, -1])

            def test_audit_failure_propagates(self):
                failure = RuntimeError("audit unavailable")
                def audit(*unused):
                    raise failure
                with self.assertRaises(RuntimeError) as caught:
                    summarize([2], audit)
                self.assertIs(caught.exception, failure)

            def test_snapshot_survives_callback_mutation(self):
                values = [2, 3]
                def audit(*unused):
                    values[:] = ["changed"]
                self.assertEqual(summarize(values, audit), {"count": 2, "total": 5})

            def test_original_list_iterated_once(self):
                class OnePass(list):
                    calls = 0
                    def __iter__(self):
                        self.calls += 1
                        if self.calls > 1:
                            raise RuntimeError("request iterated twice")
                        return super().__iter__()
                values = OnePass([2, 4])
                self.assertEqual(summarize(values)["total"], 6)
                self.assertEqual(values.calls, 1)
    ''',
    ["有效批次审计恰好一次", "外部 list 与严格整数检查", "回调异常传播", "所有权快照与单次输入迭代"],
)


case(
    "d02", "mixed_bypass_paths",
    "报价服务有 parsed 和 raw 两条路径。parsed 已经通过 parser.parse 生成不可变元组，"
    "raw 是尚未验证的导入预览。请消除 parsed 路径重复的 checked 审计，保持 raw 路径的"
    "必要检查；每个有效请求两种模式都只写一次 audit('checked', 数量)。两种模式都只接受"
    "list 输入，元素严格为非负 int（不接受 bool）；非法输入抛 QuoteError，且不写成功审计。"
    "返回 {'count': n, 'total': 总额}，不修改原 list。未知模式抛 ValueError，回调的意外错误继续传播。",
    {
        "parser.py": '''
            class QuoteError(ValueError):
                pass


            def own_list(payload):
                if not isinstance(payload, list):
                    raise QuoteError("expected list")
                return tuple(payload)


            def check_values(values, audit):
                if any(type(value) is not int or value < 0 for value in values):
                    raise QuoteError("expected nonnegative integers")
                if audit is not None:
                    audit("checked", len(values))
                return values


            def parse(payload, audit):
                return check_values(own_list(payload), audit)
        ''',
        "app.py": '''
            from parser import check_values, own_list, parse


            def _preview(values, audit):
                # Raw import preview reaches this helper without parser.parse.
                values = check_values(values, audit)
                return {"count": len(values), "total": sum(values)}


            def quote(mode, payload, audit=None):
                """Build one quote for a parsed request or raw import preview.

                Neither branch may write to the request list. Audit callbacks
                belong to the caller and may fail independently of arithmetic.
                """
                if mode == "parsed":
                    values = parse(payload, audit)
                elif mode == "raw":
                    values = own_list(payload)
                else:
                    raise ValueError("unknown mode")
                return _preview(values, audit)
        ''',
    },
    {
        "app.py": '''
            from parser import check_values, own_list, parse


            def quote(mode, payload, audit=None):
                if mode == "parsed":
                    values = parse(payload, audit)
                elif mode == "raw":
                    values = check_values(own_list(payload), audit)
                else:
                    raise ValueError("unknown mode")
                return {"count": len(values), "total": sum(values)}
        ''',
    },
    '''
        import unittest
        from app import quote
        from parser import QuoteError


        class Contract(unittest.TestCase):
            def test_parsed_values(self):
                self.assertEqual(quote("parsed", [2, 4]), {"count": 2, "total": 6})

            def test_raw_values(self):
                self.assertEqual(quote("raw", [2, 4]), {"count": 2, "total": 6})

            def test_once_on_each_path(self):
                for mode in ("parsed", "raw"):
                    events = []
                    quote(mode, [1, 2], lambda *e: events.append(e))
                    self.assertEqual(events, [("checked", 2)], mode)

            def test_empty_on_each_path(self):
                for mode in ("parsed", "raw"):
                    events = []
                    self.assertEqual(quote(mode, [], lambda *e: events.append(e)), {"count": 0, "total": 0})
                    self.assertEqual(events, [("checked", 0)], mode)

            def test_raw_invalid_values(self):
                for value in (True, -1, 1.5, "2", None):
                    with self.subTest(value=value), self.assertRaises(QuoteError):
                        quote("raw", [value])

            def test_parsed_invalid_values(self):
                for value in (False, -1, 2.5, "3", None):
                    with self.subTest(value=value), self.assertRaises(QuoteError):
                        quote("parsed", [value])

            def test_non_list_boundary_each_path(self):
                for mode in ("parsed", "raw"):
                    for value in (None, (1, 2), "12"):
                        with self.subTest(mode=mode, value=value), self.assertRaises(QuoteError):
                            quote(mode, value)

            def test_invalid_does_not_audit(self):
                for mode in ("parsed", "raw"):
                    events = []
                    with self.assertRaises(QuoteError):
                        quote(mode, [-2], lambda *e: events.append(e))
                    self.assertEqual(events, [])

            def test_unknown_mode_before_callback(self):
                events = []
                with self.assertRaises(ValueError):
                    quote("other", [1], lambda *e: events.append(e))
                self.assertEqual(events, [])

            def test_no_mutation(self):
                for mode in ("parsed", "raw"):
                    values = [3, 2]
                    quote(mode, values)
                    self.assertEqual(values, [3, 2])

            def test_callback_failure(self):
                failure = RuntimeError("audit failed")
                def audit(*unused):
                    raise failure
                for mode in ("parsed", "raw"):
                    with self.assertRaises(RuntimeError) as caught:
                        quote(mode, [2], audit)
                    self.assertIs(caught.exception, failure)
    ''',
    ["parsed 只进行一次成功校验审计", "raw 绕过路径仍拒绝无效值", "输入所有权", "未知模式与意外异常传播"],
)


case(
    "d03", "ownership_snapshots",
    "后台任务读取的路由缓存会受到外部 list 变动影响。请修复 RouteCache 的所有权边界："
    "configure 接受 dict，键是非空 str，值是由非空 str 组成的 list（允许空 list）；"
    "其他输入抛 ConfigError。成功配置必须形成独立快照；之后调用者修改原 dict 或嵌套 list 不得影响缓存。"
    "targets_for 返回可由调用者修改的新 list，修改返回值也不得污染缓存。未知路由保持 KeyError。"
    "无效的新配置不能覆盖已生效配置；输入迭代中的意外异常继续传播。保留 build_job 的排序行为。",
    {
        "cache.py": '''
            class ConfigError(ValueError):
                pass


            class RouteCache:
                def __init__(self):
                    self._routes = {}

                def configure(self, routes):
                    if not isinstance(routes, dict):
                        raise ConfigError("expected dict")
                    for name, targets in routes.items():
                        if not isinstance(name, str) or not name:
                            raise ConfigError("invalid route name")
                        if not isinstance(targets, list):
                            raise ConfigError("expected target list")
                        if any(not isinstance(target, str) or not target for target in targets):
                            raise ConfigError("invalid target")
                    self._routes = dict(routes)

                def targets_for(self, name):
                    return self._routes[name]
        ''',
        "app.py": '''
            from cache import RouteCache


            def build_job(cache, route):
                """The dispatcher is allowed to reorder its local targets.

                The configured order still belongs to the cache and is useful
                to other consumers. A dispatcher job owns the returned list.
                """
                targets = cache.targets_for(route)
                targets.sort()
                return {"route": route, "targets": targets}


            def new_cache(routes):
                cache = RouteCache()
                cache.configure(routes)
                return cache
        ''',
    },
    {
        "cache.py": '''
            class ConfigError(ValueError):
                pass


            class RouteCache:
                def __init__(self):
                    self._routes = {}

                def configure(self, routes):
                    if not isinstance(routes, dict):
                        raise ConfigError("expected dict")
                    owned = {}
                    for name, targets in routes.items():
                        if not isinstance(name, str) or not name:
                            raise ConfigError("invalid route name")
                        if not isinstance(targets, list):
                            raise ConfigError("expected target list")
                        snapshot = list(targets)
                        if any(not isinstance(target, str) or not target for target in snapshot):
                            raise ConfigError("invalid target")
                        owned[name] = snapshot
                    self._routes = owned

                def targets_for(self, name):
                    return list(self._routes[name])
        ''',
    },
    '''
        import unittest
        from app import build_job, new_cache
        from cache import RouteCache, ConfigError


        class Contract(unittest.TestCase):
            def test_config_and_get(self):
                self.assertEqual(new_cache({"r": ["b", "a"]}).targets_for("r"), ["b", "a"])

            def test_input_nested_list_is_owned(self):
                values = ["a"]
                cache = new_cache({"r": values})
                values.append("b")
                self.assertEqual(cache.targets_for("r"), ["a"])

            def test_input_dictionary_is_owned(self):
                values = {"r": ["a"]}
                cache = new_cache(values)
                values.clear()
                self.assertEqual(cache.targets_for("r"), ["a"])

            def test_output_list_is_owned(self):
                cache = new_cache({"r": ["a"]})
                output = cache.targets_for("r")
                output.append("b")
                self.assertEqual(cache.targets_for("r"), ["a"])

            def test_dispatcher_sort_does_not_reorder_cache(self):
                cache = new_cache({"r": ["b", "a"]})
                self.assertEqual(build_job(cache, "r")["targets"], ["a", "b"])
                self.assertEqual(cache.targets_for("r"), ["b", "a"])

            def test_empty_targets_and_config(self):
                cache = new_cache({"disabled": []})
                self.assertEqual(cache.targets_for("disabled"), [])
                cache.configure({})
                with self.assertRaises(KeyError):
                    cache.targets_for("disabled")

            def test_missing_route(self):
                with self.assertRaises(KeyError):
                    new_cache({}).targets_for("missing")

            def test_invalid_config_shapes(self):
                for value in (None, [], {"": ["a"]}, {2: ["a"]}, {"r": "a"}, {"r": ("a",)}):
                    with self.subTest(value=value), self.assertRaises(ConfigError):
                        new_cache(value)

            def test_invalid_targets(self):
                for value in ("", 1, True, None):
                    with self.subTest(value=value), self.assertRaises(ConfigError):
                        new_cache({"r": [value]})

            def test_invalid_reconfiguration_is_atomic(self):
                cache = new_cache({"old": ["a"]})
                with self.assertRaises(ConfigError):
                    cache.configure({"new": ["b"], "broken": [None]})
                self.assertEqual(cache.targets_for("old"), ["a"])
                with self.assertRaises(KeyError):
                    cache.targets_for("new")

            def test_iteration_failure_propagates_and_keeps_old(self):
                class Broken(list):
                    def __iter__(self):
                        raise RuntimeError("target source failed")
                cache = new_cache({"old": ["a"]})
                with self.assertRaises(RuntimeError):
                    cache.configure({"r": Broken(["x"])})
                self.assertEqual(cache.targets_for("old"), ["a"])
    ''',
    ["输入和输出 list 所有权隔离", "任务排序不污染缓存", "无效配置原子失败", "外部边界与异常传播"],
)


case(
    "d04", "boundary_error_translation",
    "请修复 get_profile 把所有存储故障都当成不存在的问题。用户 ID 必须为非空 str，非法时"
    "抛 RequestError 且不访问存储。只有 storage.MissingRecord 表示可选缺失并返回 None；"
    "storage.StoreUnavailable 需要翻译成 ServiceUnavailable，并保留原异常为 __cause__。"
    "现有 normalize 的数据损坏异常 CorruptRecord 和其他意外异常必须继续传播，不得变为空结果。"
    "成功时仍返回 name/age 两个字段，name 按现有规则 strip，age 为非负严格 int。每次请求只访问存储一次。",
    {
        "storage.py": '''
            class MissingRecord(KeyError):
                pass


            class StoreUnavailable(OSError):
                pass


            class MemoryStore:
                def __init__(self, records):
                    self.records = records
                    self.calls = []

                def fetch(self, user_id):
                    self.calls.append(user_id)
                    if user_id not in self.records:
                        raise MissingRecord(user_id)
                    return self.records[user_id]
        ''',
        "app.py": '''
            from storage import MissingRecord, StoreUnavailable


            class RequestError(ValueError):
                pass


            class CorruptRecord(ValueError):
                pass


            class ServiceUnavailable(RuntimeError):
                pass


            def normalize(record):
                if not isinstance(record, dict):
                    raise CorruptRecord("expected stored object")
                name, age = record.get("name"), record.get("age")
                if not isinstance(name, str) or not name.strip():
                    raise CorruptRecord("invalid stored name")
                if type(age) is not int or age < 0:
                    raise CorruptRecord("invalid stored age")
                return {"name": name.strip(), "age": age}


            def get_profile(store, user_id):
                if not isinstance(user_id, str) or not user_id:
                    raise RequestError("invalid user id")
                try:
                    return normalize(store.fetch(user_id))
                except Exception:
                    return None
        ''',
    },
    {
        "app.py": '''
            from storage import MissingRecord, StoreUnavailable


            class RequestError(ValueError):
                pass


            class CorruptRecord(ValueError):
                pass


            class ServiceUnavailable(RuntimeError):
                pass


            def normalize(record):
                if not isinstance(record, dict):
                    raise CorruptRecord("expected stored object")
                name, age = record.get("name"), record.get("age")
                if not isinstance(name, str) or not name.strip():
                    raise CorruptRecord("invalid stored name")
                if type(age) is not int or age < 0:
                    raise CorruptRecord("invalid stored age")
                return {"name": name.strip(), "age": age}


            def get_profile(store, user_id):
                if not isinstance(user_id, str) or not user_id:
                    raise RequestError("invalid user id")
                try:
                    record = store.fetch(user_id)
                except MissingRecord:
                    return None
                except StoreUnavailable as error:
                    raise ServiceUnavailable("profile storage unavailable") from error
                return normalize(record)
        ''',
    },
    '''
        import asyncio
        import unittest
        from app import get_profile, RequestError, CorruptRecord, ServiceUnavailable
        from storage import MemoryStore, MissingRecord, StoreUnavailable


        class FailingStore:
            def __init__(self, error):
                self.error, self.calls = error, 0
            def fetch(self, user_id):
                self.calls += 1
                raise self.error


        class Contract(unittest.TestCase):
            def test_normal(self):
                store = MemoryStore({"u": {"name": " Ada ", "age": 0, "internal": 9}})
                self.assertEqual(get_profile(store, "u"), {"name": "Ada", "age": 0})
                self.assertEqual(store.calls, ["u"])

            def test_optional_missing(self):
                store = MemoryStore({})
                self.assertIsNone(get_profile(store, "u"))
                self.assertEqual(store.calls, ["u"])

            def test_missing_subclass(self):
                class NotPresent(MissingRecord):
                    pass
                self.assertIsNone(get_profile(FailingStore(NotPresent("u")), "u"))

            def test_availability_translation_and_cause(self):
                failure = StoreUnavailable("offline")
                store = FailingStore(failure)
                with self.assertRaises(ServiceUnavailable) as caught:
                    get_profile(store, "u")
                self.assertIs(caught.exception.__cause__, failure)
                self.assertEqual(store.calls, 1)

            def test_unexpected_store_error(self):
                failure = RuntimeError("programming error")
                with self.assertRaises(RuntimeError) as caught:
                    get_profile(FailingStore(failure), "u")
                self.assertIs(caught.exception, failure)

            def test_invalid_ids_before_storage(self):
                for value in (None, "", 1, False):
                    store = MemoryStore({})
                    with self.subTest(value=value), self.assertRaises(RequestError):
                        get_profile(store, value)
                    self.assertEqual(store.calls, [])

            def test_none_record_is_corruption(self):
                with self.assertRaises(CorruptRecord):
                    get_profile(MemoryStore({"u": None}), "u")

            def test_malformed_record(self):
                for record in ({}, {"name": " ", "age": 1}, {"name": "A", "age": True}, {"name": "A", "age": -1}):
                    with self.subTest(record=record), self.assertRaises(CorruptRecord):
                        get_profile(MemoryStore({"u": record}), "u")

            def test_normalization_bug_propagates(self):
                class Broken(dict):
                    def get(self, key, default=None):
                        raise RuntimeError("decode failure")
                with self.assertRaises(RuntimeError):
                    get_profile(MemoryStore({"u": Broken()}), "u")

            def test_cancellation_propagates(self):
                failure = asyncio.CancelledError()
                with self.assertRaises(asyncio.CancelledError) as caught:
                    get_profile(FailingStore(failure), "u")
                self.assertIs(caught.exception, failure)

            def test_success_does_not_mutate_record(self):
                record = {"name": " A ", "age": 1}
                get_profile(MemoryStore({"u": record}), "u")
                self.assertEqual(record, {"name": " A ", "age": 1})
    ''',
    ["可选缺失与真实故障区分", "错误翻译保留 cause", "数据损坏和意外异常传播", "非法外部输入不访问存储"],
)


case(
    "d05", "bounded_idempotent_retry",
    "save_document 的重试会放大永久故障，并可能重复无幂等键写入。请按现有接口修复："
    "仅 TransientWriteError 可重试，且必须有非空 idempotency_key；None 表示一次性写入，"
    "该次失败必须直接传播。WriteRejected、AmbiguousWriteError、其他错误或取消不得重试。"
    "max_attempts 包括第一次调用，为严格 int、范围 1..5；非法值抛 ValueError。"
    "幂等键只允许 None 或 strip 后非空的 str，但传给客户端时保持原键不变。"
    "保留现有 0.1、0.2、0.4... 秒退避策略，通过注入的 sleep 调用，最后失败后不再等待。"
    "每次尝试传相同 payload 和 key，成功结果原样返回；耗尽次数时传播最后的原异常。",
    {
        "storage.py": '''
            class TransientWriteError(OSError):
                """Retryable only when the request carries an idempotency key."""


            class WriteRejected(ValueError):
                pass


            class AmbiguousWriteError(RuntimeError):
                pass


            class DocumentStore:
                def __init__(self):
                    self.documents = []
                    self.receipts = {}

                def write_document(self, payload, idempotency_key=None):
                    if idempotency_key is not None and idempotency_key in self.receipts:
                        return self.receipts[idempotency_key]
                    receipt = "doc-" + str(len(self.documents) + 1)
                    self.documents.append(dict(payload))
                    if idempotency_key is not None:
                        self.receipts[idempotency_key] = receipt
                    return receipt
        ''',
        "app.py": '''
            import time
            from storage import TransientWriteError


            def save_document(client, payload, idempotency_key=None, max_attempts=3, sleep=time.sleep):
                if type(max_attempts) is not int or not 1 <= max_attempts <= 5:
                    raise ValueError("max_attempts must be an integer from 1 to 5")
                if idempotency_key is not None and (
                    not isinstance(idempotency_key, str) or not idempotency_key.strip()
                ):
                    raise ValueError("invalid idempotency key")
                for attempt in range(1, max_attempts + 1):
                    try:
                        return client.write_document(payload, idempotency_key=idempotency_key)
                    except Exception:
                        if attempt == max_attempts:
                            raise
                        sleep(0.1 * (2 ** (attempt - 1)))
        ''',
    },
    {
        "app.py": '''
            import time
            from storage import TransientWriteError


            def save_document(client, payload, idempotency_key=None, max_attempts=3, sleep=time.sleep):
                if type(max_attempts) is not int or not 1 <= max_attempts <= 5:
                    raise ValueError("max_attempts must be an integer from 1 to 5")
                if idempotency_key is not None and (
                    not isinstance(idempotency_key, str) or not idempotency_key.strip()
                ):
                    raise ValueError("invalid idempotency key")
                for attempt in range(1, max_attempts + 1):
                    try:
                        return client.write_document(payload, idempotency_key=idempotency_key)
                    except TransientWriteError:
                        if idempotency_key is None or attempt == max_attempts:
                            raise
                        sleep(0.1 * (2 ** (attempt - 1)))
        ''',
    },
    '''
        import asyncio
        import unittest
        from app import save_document
        from storage import DocumentStore, TransientWriteError, WriteRejected, AmbiguousWriteError


        class Scripted:
            def __init__(self, responses):
                self.responses, self.calls = list(responses), []
            def write_document(self, payload, idempotency_key=None):
                self.calls.append((payload, idempotency_key))
                value = self.responses.pop(0)
                if isinstance(value, BaseException):
                    raise value
                return value


        class Contract(unittest.TestCase):
            def test_immediate_success(self):
                receipt = object()
                client, waits = Scripted([receipt]), []
                self.assertIs(save_document(client, {"x": 1}, "key", sleep=waits.append), receipt)
                self.assertEqual(len(client.calls), 1)
                self.assertEqual(waits, [])

            def test_retry_transient_with_key(self):
                client, waits = Scripted([TransientWriteError(), TransientWriteError(), "ok"]), []
                payload = {"x": 1}
                self.assertEqual(save_document(client, payload, " key ", sleep=waits.append), "ok")
                self.assertEqual(waits, [0.1, 0.2])
                self.assertEqual(len(client.calls), 3)
                self.assertTrue(all(p is payload and key == " key " for p, key in client.calls))
                self.assertEqual(payload, {"x": 1})

            def test_exhaustion_raises_last(self):
                first, last = TransientWriteError("a"), TransientWriteError("b")
                client, waits = Scripted([first, last]), []
                with self.assertRaises(TransientWriteError) as caught:
                    save_document(client, {}, "key", max_attempts=2, sleep=waits.append)
                self.assertIs(caught.exception, last)
                self.assertEqual(waits, [0.1])
                self.assertEqual(len(client.calls), 2)

            def test_none_key_never_retries(self):
                error = TransientWriteError("unknown result")
                client, waits = Scripted([error, "unexpected second call"]), []
                with self.assertRaises(TransientWriteError) as caught:
                    save_document(client, {}, sleep=waits.append)
                self.assertIs(caught.exception, error)
                self.assertEqual(len(client.calls), 1)
                self.assertEqual(waits, [])

            def test_permanent_error_never_retries(self):
                error = WriteRejected("invalid document")
                client, waits = Scripted([error, "unexpected retry"]), []
                with self.assertRaises(WriteRejected) as caught:
                    save_document(client, {}, "key", sleep=waits.append)
                self.assertIs(caught.exception, error)
                self.assertEqual(len(client.calls), 1)
                self.assertEqual(waits, [])

            def test_ambiguous_error_never_retries(self):
                error = AmbiguousWriteError("not classified as retryable")
                client = Scripted([error, "unexpected retry"])
                with self.assertRaises(AmbiguousWriteError) as caught:
                    save_document(client, {}, "key", sleep=lambda unused: None)
                self.assertIs(caught.exception, error)
                self.assertEqual(len(client.calls), 1)

            def test_unexpected_error_never_retries(self):
                error = RuntimeError("bug")
                client = Scripted([error, "unexpected retry"])
                with self.assertRaises(RuntimeError) as caught:
                    save_document(client, {}, "key", sleep=lambda unused: None)
                self.assertIs(caught.exception, error)
                self.assertEqual(len(client.calls), 1)

            def test_cancellation(self):
                error = asyncio.CancelledError()
                client = Scripted([error])
                with self.assertRaises(asyncio.CancelledError) as caught:
                    save_document(client, {}, "key", sleep=lambda unused: None)
                self.assertIs(caught.exception, error)
                self.assertEqual(len(client.calls), 1)

            def test_attempts_one_has_no_wait(self):
                client, waits = Scripted([TransientWriteError()]), []
                with self.assertRaises(TransientWriteError):
                    save_document(client, {}, "key", max_attempts=1, sleep=waits.append)
                self.assertEqual(len(client.calls), 1)
                self.assertEqual(waits, [])

            def test_invalid_policy_before_write(self):
                for limit in (0, 6, -1, True, 2.0, "3"):
                    client = Scripted(["never"])
                    with self.subTest(limit=limit), self.assertRaises(ValueError):
                        save_document(client, {}, "key", max_attempts=limit)
                    self.assertEqual(client.calls, [])

            def test_invalid_key_before_write(self):
                for key in ("", "  ", False, 1):
                    client = Scripted(["never"])
                    with self.subTest(key=key), self.assertRaises(ValueError):
                        save_document(client, {}, key)
                    self.assertEqual(client.calls, [])

            def test_committed_transient_is_deduplicated(self):
                class CommitThenFail(DocumentStore):
                    calls = 0
                    def write_document(self, payload, idempotency_key=None):
                        self.calls += 1
                        result = super().write_document(payload, idempotency_key)
                        if self.calls == 1:
                            raise TransientWriteError("reply lost after commit")
                        return result
                store = CommitThenFail()
                self.assertEqual(save_document(store, {"x": 2}, "k", sleep=lambda unused: None), "doc-1")
                self.assertEqual(store.documents, [{"x": 2}])
                self.assertEqual(store.calls, 2)
    ''',
    ["仅瞬态且有幂等键时重试", "有界次数和既定退避", "永久/未知/取消异常不重试", "提交后响应丢失不重复写入"],
)


case(
    "d06", "sqlite_transaction_cleanup",
    "请修复 transfer 可能只扣款不入账以及异常泄漏连接的问题。factory 返回本模块约定的"
    " SQLite autocommit 连接；转账必须在一个原子事务内完成，成功提交，任何异常或取消都回滚并释放连接。"
    "释放是调用 close 一次；成功获取连接后无论成功、业务失败或 SQL 失败都应释放。"
    "src/dst 必须是不同的非空 str，amount 必须是严格正 int（bool 不接受），非法参数抛 ValueError 且不得打开连接。"
    "缺失任一账户抛 UnknownAccount，余额不足抛 InsufficientFunds，其他错误保持传播。"
    "SQL 参数必须按数据处理；返回值仍是 None。不要用提交部分结果的方式恢复。",
    {
        "database.py": '''
            import sqlite3


            class UnknownAccount(LookupError):
                pass


            class InsufficientFunds(ValueError):
                pass


            def connect(path):
                # Explicit BEGIN/COMMIT is needed because this is autocommit.
                return sqlite3.connect(path, isolation_level=None)


            def validate_transfer(src, dst, amount):
                if not isinstance(src, str) or not src or not isinstance(dst, str) or not dst or src == dst:
                    raise ValueError("invalid accounts")
                if type(amount) is not int or amount <= 0:
                    raise ValueError("amount must be a positive integer")
        ''',
        "app.py": '''
            from database import UnknownAccount, InsufficientFunds, validate_transfer


            def transfer(factory, src, dst, amount):
                validate_transfer(src, dst, amount)
                connection = factory()
                row = connection.execute("SELECT cents FROM accounts WHERE id = ?", (src,)).fetchone()
                if row is None:
                    raise UnknownAccount(src)
                if row[0] < amount:
                    raise InsufficientFunds(src)
                connection.execute("UPDATE accounts SET cents = cents - ? WHERE id = ?", (amount, src))
                connection.commit()
                result = connection.execute("UPDATE accounts SET cents = cents + ? WHERE id = ?", (amount, dst))
                if result.rowcount != 1:
                    raise UnknownAccount(dst)
                connection.commit()
                connection.close()
        ''',
    },
    {
        "app.py": '''
            from database import UnknownAccount, InsufficientFunds, validate_transfer


            def transfer(factory, src, dst, amount):
                validate_transfer(src, dst, amount)
                connection = factory()
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    row = connection.execute("SELECT cents FROM accounts WHERE id = ?", (src,)).fetchone()
                    if row is None:
                        raise UnknownAccount(src)
                    if row[0] < amount:
                        raise InsufficientFunds(src)
                    result = connection.execute("UPDATE accounts SET cents = cents - ? WHERE id = ?", (amount, src))
                    result = connection.execute("UPDATE accounts SET cents = cents + ? WHERE id = ?", (amount, dst))
                    if result.rowcount != 1:
                        raise UnknownAccount(dst)
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise
                finally:
                    connection.close()
        ''',
    },
    '''
        import sqlite3
        import tempfile
        from pathlib import Path
        import unittest
        from app import transfer
        from database import connect, UnknownAccount, InsufficientFunds


        class Tracked:
            def __init__(self, connection, fail_commit=False, interrupt_credit=False):
                self.inner, self.closed, self.rollbacks = connection, 0, 0
                self.fail_commit, self.interrupt_credit = fail_commit, interrupt_credit
            def execute(self, sql, params=()):
                if self.interrupt_credit and "cents +" in sql:
                    raise KeyboardInterrupt("cancel transfer")
                return self.inner.execute(sql, params)
            def commit(self):
                if self.fail_commit:
                    raise sqlite3.OperationalError("commit rejected")
                return self.inner.commit()
            def rollback(self):
                self.rollbacks += 1
                return self.inner.rollback()
            def close(self):
                self.closed += 1
                self.inner.close()


        class Contract(unittest.TestCase):
            def setUp(self):
                self.temporary = tempfile.TemporaryDirectory()
                self.path = Path(self.temporary.name) / "ledger.db"
                self.connections = []
                conn = connect(self.path)
                try:
                    conn.execute("CREATE TABLE accounts (id TEXT PRIMARY KEY, cents INTEGER NOT NULL)")
                    conn.executemany("INSERT INTO accounts VALUES (?, ?)", [("A", 100), ("B", 20)])
                finally:
                    conn.close()
            def tearDown(self):
                for conn in self.connections:
                    if not conn.closed:
                        conn.close()
                self.temporary.cleanup()
            def factory(self, **flags):
                conn = Tracked(connect(self.path), **flags)
                self.connections.append(conn)
                return conn
            def balances(self):
                conn = connect(self.path)
                try:
                    return dict(conn.execute("SELECT id, cents FROM accounts"))
                finally:
                    conn.close()
            def assert_closed(self):
                self.assertEqual(len(self.connections), 1)
                self.assertEqual(self.connections[0].closed, 1)

            def test_success(self):
                self.assertIsNone(transfer(self.factory, "A", "B", 30))
                self.assertEqual(self.balances(), {"A": 70, "B": 50})
                self.assert_closed()

            def test_missing_destination_rolls_back(self):
                with self.assertRaises(UnknownAccount):
                    transfer(self.factory, "A", "missing", 30)
                self.assertEqual(self.balances(), {"A": 100, "B": 20})
                self.assert_closed()

            def test_missing_source_closes(self):
                with self.assertRaises(UnknownAccount):
                    transfer(self.factory, "missing", "B", 30)
                self.assertEqual(self.balances(), {"A": 100, "B": 20})
                self.assert_closed()

            def test_insufficient_funds(self):
                with self.assertRaises(InsufficientFunds):
                    transfer(self.factory, "A", "B", 101)
                self.assertEqual(self.balances(), {"A": 100, "B": 20})
                self.assert_closed()

            def test_credit_sql_failure_is_atomic(self):
                conn = connect(self.path)
                try:
                    conn.execute("CREATE TRIGGER reject_credit BEFORE UPDATE OF cents ON accounts WHEN NEW.id = 'B' BEGIN SELECT RAISE(ABORT, 'credit rejected'); END")
                finally:
                    conn.close()
                with self.assertRaises(sqlite3.IntegrityError):
                    transfer(self.factory, "A", "B", 30)
                self.assertEqual(self.balances(), {"A": 100, "B": 20})
                self.assert_closed()

            def test_commit_failure_rolls_back(self):
                with self.assertRaises(sqlite3.OperationalError):
                    transfer(lambda: self.factory(fail_commit=True), "A", "B", 30)
                self.assertEqual(self.balances(), {"A": 100, "B": 20})
                self.assert_closed()

            def test_interrupt_rolls_back_and_propagates(self):
                with self.assertRaises(KeyboardInterrupt):
                    transfer(lambda: self.factory(interrupt_credit=True), "A", "B", 30)
                self.assertEqual(self.balances(), {"A": 100, "B": 20})
                self.assert_closed()

            def test_invalid_amount_before_open(self):
                for amount in (0, -1, True, 1.0, "1"):
                    with self.subTest(amount=amount), self.assertRaises(ValueError):
                        transfer(self.factory, "A", "B", amount)
                self.assertEqual(self.connections, [])

            def test_invalid_account_before_open(self):
                for src, dst in ((None, "B"), ("", "B"), ("A", ""), ("A", 2), ("A", "A")):
                    with self.subTest(src=src, dst=dst), self.assertRaises(ValueError):
                        transfer(self.factory, src, dst, 1)
                self.assertEqual(self.connections, [])

            def test_sql_looking_id_is_data(self):
                with self.assertRaises(UnknownAccount):
                    transfer(self.factory, "A' OR 1=1 --", "B", 1)
                self.assertEqual(self.balances(), {"A": 100, "B": 20})
                self.assert_closed()

            def test_factory_failure_propagates(self):
                failure = sqlite3.OperationalError("cannot connect")
                def factory():
                    raise failure
                with self.assertRaises(sqlite3.OperationalError) as caught:
                    transfer(factory, "A", "B", 1)
                self.assertIs(caught.exception, failure)

            def test_full_balance_transfer(self):
                transfer(self.factory, "A", "B", 100)
                self.assertEqual(self.balances(), {"A": 0, "B": 120})
                self.assert_closed()
    ''',
    ["真实 SQLite 事务原子性", "SQL/提交/取消故障回滚", "所有已获取连接恰好关闭一次", "参数化SQL及外部参数验证"],
)


case(
    "d07", "async_cancellation_cleanup",
    "read_page 目前在源耗尽、读取故障或任务取消时都返回空 list，并可能不释放资源。"
    "请保持最多读取 limit 项的分页行为：源正常耗尽返回已收集前缀，读取中的意外异常和取消原样传播。"
    "成功 await source.open() 后必须恰好 await source.close() 一次，无论正常、耗尽、错误还是在 read 等待期间"
    "收到一次 task.cancel()。open 本身失败时不得 close 未获取资源。limit 为非负严格 int（bool 不接受），"
    "非法值抛 ValueError；limit=0 返回 [] 且不打开资源。不要多消费 limit 之外的数据。",
    {
        "stream.py": '''
            import asyncio


            class MemorySource:
                def __init__(self, values):
                    self.values = iter(values)
                    self.opened = False
                    self.opens = 0
                    self.closes = 0
                    self.reads = 0

                async def open(self):
                    await asyncio.sleep(0)
                    self.opens += 1
                    self.opened = True

                async def read(self):
                    if not self.opened:
                        raise RuntimeError("source is not open")
                    self.reads += 1
                    try:
                        return next(self.values)
                    except StopIteration:
                        raise StopAsyncIteration from None

                async def close(self):
                    await asyncio.sleep(0)
                    self.closes += 1
                    self.opened = False
        ''',
        "app.py": '''
            async def read_page(source, limit):
                if type(limit) is not int or limit < 0:
                    raise ValueError("limit must be a nonnegative integer")
                if limit == 0:
                    return []
                await source.open()
                values = []
                try:
                    for unused in range(limit):
                        values.append(await source.read())
                except BaseException:
                    return []
                await source.close()
                return values
        ''',
    },
    {
        "app.py": '''
            async def read_page(source, limit):
                if type(limit) is not int or limit < 0:
                    raise ValueError("limit must be a nonnegative integer")
                if limit == 0:
                    return []
                await source.open()
                values = []
                try:
                    for unused in range(limit):
                        try:
                            values.append(await source.read())
                        except StopAsyncIteration:
                            break
                    return values
                finally:
                    await source.close()
        ''',
    },
    '''
        import asyncio
        import unittest
        from app import read_page
        from stream import MemorySource


        class Contract(unittest.IsolatedAsyncioTestCase):
            async def test_exact_limit(self):
                source = MemorySource([1, 2, 3])
                self.assertEqual(await read_page(source, 2), [1, 2])
                self.assertEqual((source.opens, source.closes, source.reads), (1, 1, 2))

            async def test_exhaustion_returns_prefix(self):
                source = MemorySource([1, 2])
                self.assertEqual(await read_page(source, 5), [1, 2])
                self.assertEqual((source.opens, source.closes), (1, 1))
                self.assertEqual(source.reads, 3)

            async def test_empty_source_closes(self):
                source = MemorySource([])
                self.assertEqual(await read_page(source, 1), [])
                self.assertEqual((source.opens, source.closes), (1, 1))

            async def test_zero_does_not_open(self):
                source = MemorySource([1])
                self.assertEqual(await read_page(source, 0), [])
                self.assertEqual((source.opens, source.closes, source.reads), (0, 0, 0))

            async def test_invalid_limit_does_not_open(self):
                for limit in (-1, True, 1.5, "2", None):
                    source = MemorySource([1])
                    with self.subTest(limit=limit), self.assertRaises(ValueError):
                        await read_page(source, limit)
                    self.assertEqual((source.opens, source.closes), (0, 0))

            async def test_first_read_failure(self):
                failure = RuntimeError("read failed")
                class Source(MemorySource):
                    async def read(self):
                        raise failure
                source = Source([])
                with self.assertRaises(RuntimeError) as caught:
                    await read_page(source, 2)
                self.assertIs(caught.exception, failure)
                self.assertEqual(source.closes, 1)

            async def test_middle_failure_is_not_successful_prefix(self):
                failure = ValueError("corrupt event")
                class Source(MemorySource):
                    async def read(self):
                        if self.reads == 1:
                            raise failure
                        return await super().read()
                source = Source([1, 2])
                with self.assertRaises(ValueError) as caught:
                    await read_page(source, 3)
                self.assertIs(caught.exception, failure)
                self.assertEqual(source.closes, 1)

            async def test_cancellation_closes_and_propagates(self):
                class Waiting(MemorySource):
                    def __init__(self):
                        super().__init__([])
                        self.started = asyncio.Event()
                    async def read(self):
                        self.started.set()
                        await asyncio.Event().wait()
                source = Waiting()
                task = asyncio.create_task(read_page(source, 1))
                await source.started.wait()
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertEqual((source.opens, source.closes), (1, 1))
                self.assertFalse(source.opened)

            async def test_open_failure_does_not_close(self):
                failure = OSError("open failed")
                class Source(MemorySource):
                    async def open(self):
                        raise failure
                source = Source([])
                with self.assertRaises(OSError) as caught:
                    await read_page(source, 1)
                self.assertIs(caught.exception, failure)
                self.assertEqual(source.closes, 0)

            async def test_stop_during_open_is_not_normal_exhaustion(self):
                class Source(MemorySource):
                    async def open(self):
                        raise StopAsyncIteration
                source = Source([])
                with self.assertRaises(StopAsyncIteration):
                    await read_page(source, 1)
                self.assertEqual(source.closes, 0)

            async def test_none_is_an_item(self):
                source = MemorySource([None, 0, False])
                self.assertEqual(await read_page(source, 3), [None, 0, False])
                self.assertEqual(source.closes, 1)

            async def test_input_not_consumed_beyond_page(self):
                visited = []
                def items():
                    for value in (1, 2):
                        visited.append(value)
                        yield value
                source = MemorySource(items())
                self.assertEqual(await read_page(source, 1), [1])
                self.assertEqual(visited, [1])
    ''',
    ["真实异步 task.cancel 传播", "读取异常与正常耗尽区分", "获取成功后资源恰好释放一次", "获取失败与零页不释放未获取资源"],
)


case(
    "d08", "plugin_mutation_invalidates_validation",
    "process_charge 会让插件调整已通过验证的订单。请修复插件之后仍使用旧验证结论的问题。"
    "输入必须是恰好包含 amount、currency 两个键的 dict；amount 为严格 int、范围 0..100000，"
    "currency 必须严格为 str 且为 USD 或 EUR，否则抛 InputError，且非法外部输入不得进入插件或 charge。"
    "hook 可选，收到独立的浅拷贝并原地修改它，返回值忽略；因字段仅有整数和字符串，"
    "不需要通用深拷贝。插件返回后若字段非法、缺失或多出键，也必须抛 InputError 且不得调用 charge。"
    "有效修改应传给 charge；用户原 dict 在成功和失败时都不变。插件与 charge 的其他异常原样传播，"
    "charge 的成功返回值原样返回。不要把插件注释当成运行期有效性证明。",
    {
        "policy.py": '''
            class InputError(ValueError):
                pass


            def validate(payload):
                if not isinstance(payload, dict) or set(payload) != {"amount", "currency"}:
                    raise InputError("invalid payload shape")
                amount = payload["amount"]
                if type(amount) is not int or not 0 <= amount <= 100000:
                    raise InputError("invalid amount")
                if type(payload["currency"]) is not str or payload["currency"] not in ("USD", "EUR"):
                    raise InputError("invalid currency")


            def own_request(payload):
                validate(payload)
                return dict(payload)
        ''',
        "plugins.py": '''
            def loyalty_discount(payload):
                """An example hook; other hooks are supplied by integrations."""
                payload["amount"] = max(0, payload["amount"] - 5)
        ''',
        "app.py": '''
            from policy import own_request


            def process_charge(payload, charge, hook=None):
                """Validate the request and pass the owned order to an optional hook.

                Hooks can mutate their order in place. The caller still owns
                its external request and may retain it for audit logging.
                The charge function owns error translation for its transport.
                """
                owned = own_request(payload)
                if hook is not None:
                    hook(owned)
                # The original request passed validation before reaching here.
                return charge(owned["amount"], owned["currency"])
        ''',
    },
    {
        "app.py": '''
            from policy import own_request, validate


            def process_charge(payload, charge, hook=None):
                owned = own_request(payload)
                if hook is not None:
                    hook(owned)
                    validate(owned)
                return charge(owned["amount"], owned["currency"])
        ''',
    },
    '''
        import asyncio
        import unittest
        from app import process_charge
        from policy import InputError
        from plugins import loyalty_discount


        class Charger:
            def __init__(self):
                self.calls = []
                self.result = object()
            def __call__(self, amount, currency):
                self.calls.append((amount, currency))
                return self.result


        class Contract(unittest.TestCase):
            def test_normal_no_hook(self):
                charge = Charger()
                self.assertIs(process_charge({"amount": 20, "currency": "USD"}, charge), charge.result)
                self.assertEqual(charge.calls, [(20, "USD")])

            def test_valid_hook_and_input_ownership(self):
                charge, payload = Charger(), {"amount": 20, "currency": "USD"}
                process_charge(payload, charge, loyalty_discount)
                self.assertEqual(charge.calls, [(15, "USD")])
                self.assertEqual(payload, {"amount": 20, "currency": "USD"})

            def test_hook_can_change_currency(self):
                charge = Charger()
                process_charge({"amount": 2, "currency": "USD"}, charge, lambda value: value.update(currency="EUR"))
                self.assertEqual(charge.calls, [(2, "EUR")])

            def test_zero_is_valid(self):
                charge = Charger()
                process_charge({"amount": 0, "currency": "EUR"}, charge)
                self.assertEqual(charge.calls, [(0, "EUR")])

            def test_invalid_external_does_not_reach_hook_or_charge(self):
                for payload in (None, [], {}, {"amount": 1, "currency": "USD", "extra": 1}, {"amount": -1, "currency": "USD"}):
                    charge, hooks = Charger(), []
                    with self.subTest(payload=payload), self.assertRaises(InputError):
                        process_charge(payload, charge, hooks.append)
                    self.assertEqual(charge.calls, [])
                    self.assertEqual(hooks, [])

            def test_invalid_external_types(self):
                for amount in (True, 1.0, "1", 100001):
                    with self.subTest(amount=amount), self.assertRaises(InputError):
                        process_charge({"amount": amount, "currency": "USD"}, Charger())

            def test_invalid_currency_types_before_hook(self):
                for currency in (None, True, b"USD", ["USD"]):
                    charge, hooks = Charger(), []
                    with self.subTest(currency=currency), self.assertRaises(InputError):
                        process_charge({"amount": 5, "currency": currency}, charge, hooks.append)
                    self.assertEqual(charge.calls, [])
                    self.assertEqual(hooks, [])

            def test_invalid_plugin_amount_does_not_charge(self):
                for amount in (-1, True, "5", 100001):
                    charge = Charger()
                    with self.subTest(amount=amount), self.assertRaises(InputError):
                        process_charge({"amount": 5, "currency": "USD"}, charge, lambda value: value.update(amount=amount))
                    self.assertEqual(charge.calls, [])

            def test_plugin_shape_change_rejected(self):
                for hook in (lambda value: value.pop("amount"), lambda value: value.update(extra=1)):
                    charge = Charger()
                    with self.assertRaises(InputError):
                        process_charge({"amount": 5, "currency": "USD"}, charge, hook)
                    self.assertEqual(charge.calls, [])

            def test_plugin_invalid_currency_rejected(self):
                charge = Charger()
                with self.assertRaises(InputError):
                    process_charge({"amount": 5, "currency": "USD"}, charge, lambda value: value.update(currency="XXX"))
                self.assertEqual(charge.calls, [])

            def test_invalid_plugin_preserves_original(self):
                payload, charge = {"amount": 5, "currency": "USD"}, Charger()
                with self.assertRaises(InputError):
                    process_charge(payload, charge, lambda value: value.update(amount=-2))
                self.assertEqual(payload, {"amount": 5, "currency": "USD"})

            def test_plugin_exception_is_not_success(self):
                charge, failure = Charger(), RuntimeError("plugin failed")
                def hook(value):
                    raise failure
                with self.assertRaises(RuntimeError) as caught:
                    process_charge({"amount": 5, "currency": "USD"}, charge, hook)
                self.assertIs(caught.exception, failure)
                self.assertEqual(charge.calls, [])

            def test_charge_exception_propagates(self):
                failure = OSError("transport failed")
                def charge(amount, currency):
                    raise failure
                with self.assertRaises(OSError) as caught:
                    process_charge({"amount": 5, "currency": "USD"}, charge)
                self.assertIs(caught.exception, failure)

            def test_hook_return_value_is_ignored(self):
                charge = Charger()
                process_charge({"amount": 5, "currency": "USD"}, charge, lambda value: None)
                self.assertEqual(charge.calls, [(5, "USD")])

            def test_plugin_cancellation_propagates_without_charge(self):
                charge, failure = Charger(), asyncio.CancelledError()
                def hook(value):
                    raise failure
                with self.assertRaises(asyncio.CancelledError) as caught:
                    process_charge({"amount": 5, "currency": "USD"}, charge, hook)
                self.assertIs(caught.exception, failure)
                self.assertEqual(charge.calls, [])
    ''',
    ["验证前后的可变插件边界", "非法输入不调用插件或写入", "必要输入副本保留", "插件和写入异常传播"],
)


def main():
    HERE.mkdir(parents=True, exist_ok=True)
    manifest = []
    for item in CASES:
        path = HERE / (item["id"] + ".json")
        path.write_text(json.dumps(item, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        manifest.append({"id": item["id"], "family": item["family"], "critical": item["critical"],
                         "file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest().upper()})
    (HERE / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    preflight = []
    for item in CASES:
        stages = {}
        for stage in ("base", "reference"):
            with tempfile.TemporaryDirectory(prefix="fixture-", dir=HERE) as temporary:
                directory = Path(temporary).resolve()
                assert directory.is_relative_to(HERE)
                files = dict(item["files"])
                if stage == "reference":
                    files.update(item["reference_files"])
                files.update(item["hidden_tests"])
                for name, source in files.items():
                    target = directory / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(source, encoding="utf-8")
                env = os.environ.copy()
                env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8", PYTHONPATH=str(directory))
                command = [sys.executable, "-B", "-m", "unittest", "discover", "-s", ".", "-p", "test_contract.py", "-v"]
                result = subprocess.run(command, cwd=directory, env=env, capture_output=True, text=True,
                                        encoding="utf-8", timeout=30)
                text = result.stdout + result.stderr
                log = HERE / "preflight" / item["id"] / (stage + ".txt")
                log.parent.mkdir(parents=True, exist_ok=True)
                log.write_text(text, encoding="utf-8")
                match = re.search(r"Ran (\d+) tests? in", text)
                stages[stage] = {"exit_code": result.returncode, "tests_ran": int(match.group(1)) if match else None,
                                 "log": log.relative_to(HERE).as_posix(),
                                 "log_sha256": hashlib.sha256(log.read_bytes()).hexdigest().upper()}
        preflight.append({"id": item["id"], **stages})
    report = {"cases": len(CASES), "reference_tests_total": sum(x["reference"]["tests_ran"] or 0 for x in preflight),
              "base_all_rejected": all(x["base"]["exit_code"] != 0 and x["base"]["tests_ran"] for x in preflight),
              "reference_all_passed": all(x["reference"]["exit_code"] == 0 and x["reference"]["tests_ran"] for x in preflight),
              "python_version": sys.version, "results": preflight,
              "model_calls": 0, "synthetic_development_cases_only": True}
    (HERE / "preflight.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return int(not (report["base_all_rejected"] and report["reference_all_passed"]))


if __name__ == "__main__":
    raise SystemExit(main())
