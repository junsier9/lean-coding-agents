"""A new development regression for transparent cached method calls."""
from pathlib import Path
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import textwrap
import time

HERE = Path(__file__).resolve().parent
clean = lambda value: textwrap.dedent(value).strip() + "\n"

INITIAL_MEMO = clean('''
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
''')

REFERENCE_MEMO = clean('''
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
''')

ACCOUNTING = clean('''
    from memo import memoized_method

    class QuoteError(ValueError):
        pass

    class RateUnavailable(RuntimeError):
        pass

    class Account:
        def __init__(account, rate, backend=None):
            account.rate = rate
            account.backend = backend
            account.evaluations = 0

        @memoized_method
        def quote(account, units, *, fee=0):
            """Return a quote in integer cents using this account's rate."""
            account.evaluations += 1
            if type(units) is not int or units < 0:
                raise QuoteError("units")
            if type(fee) is not int or fee < 0:
                raise QuoteError("fee")
            rate = account.backend() if account.backend is not None else account.rate
            return rate * units + fee
''')

TESTS = clean('''
    import unittest
    from accounting import Account, QuoteError, RateUnavailable

    class Contract(unittest.TestCase):
        def info(self, account):
            value = Account.quote.cache_info(account)
            return value.hits, value.misses, value.size

        def test_bound_positional_call(self):
            account = Account(10)
            self.assertEqual(account.quote(3, fee=2), 32)
            self.assertEqual(self.info(account), (0, 1, 1))

        def test_bound_business_keyword_call(self):
            account = Account(10)
            self.assertEqual(account.quote(units=3, fee=2), 32)
            self.assertEqual(account.evaluations, 1)

        def test_unbound_receiver_positional_call(self):
            account = Account(10)
            self.assertEqual(Account.quote(account, 3, fee=2), 32)

        def test_unbound_receiver_keyword_call(self):
            account = Account(10)
            self.assertEqual(Account.quote(account=account, units=3, fee=2), 32)
            self.assertEqual(self.info(account), (0, 1, 1))

        def test_unbound_mixed_call(self):
            account = Account(10)
            self.assertEqual(Account.quote(account, units=3, fee=2), 32)

        def test_equivalent_forms_share_cache(self):
            account = Account(10)
            results = [account.quote(3), account.quote(units=3),
                       Account.quote(account, 3, fee=0),
                       Account.quote(account=account, units=3),
                       Account.quote(account, units=3, fee=0)]
            self.assertEqual(results, [30] * 5)
            self.assertEqual(account.evaluations, 1)
            self.assertEqual(self.info(account), (4, 1, 1))

        def test_cache_hit_skips_business_backend(self):
            calls = []
            def backend():
                calls.append(1)
                return 7
            account = Account(0, backend)
            self.assertEqual(account.quote(2), 14)
            self.assertEqual(account.quote(2), 14)
            self.assertEqual(calls, [1])
            self.assertEqual(self.info(account), (1, 1, 1))

        def test_public_wrapped_bypasses_cache_positionally(self):
            account = Account(10)
            self.assertEqual(account.quote(2), 20)
            before = self.info(account)
            self.assertEqual(Account.quote.__wrapped__(account, 2), 20)
            self.assertEqual(account.evaluations, 2)
            self.assertEqual(self.info(account), before)

        def test_public_wrapped_bypasses_cache_with_receiver_keyword(self):
            account = Account(10)
            self.assertEqual(account.quote(2), 20)
            before = self.info(account)
            self.assertEqual(account.quote.__wrapped__(account=account, units=2), 20)
            self.assertEqual(account.evaluations, 2)
            self.assertEqual(self.info(account), before)

        def test_instance_cache_and_statistics_are_isolated(self):
            first, second = Account(10), Account(20)
            self.assertEqual(first.quote(2), 20)
            self.assertEqual(first.quote(2), 20)
            self.assertEqual(second.quote(2), 40)
            self.assertEqual((first.evaluations, second.evaluations), (1, 1))
            self.assertEqual(self.info(first), (1, 1, 1))
            self.assertEqual(self.info(second), (0, 1, 1))

        def test_clear_affects_one_instance_only(self):
            first, second = Account(10), Account(20)
            first.quote(2)
            second.quote(2)
            Account.quote.cache_clear(first)
            self.assertEqual(self.info(first), (0, 0, 0))
            self.assertEqual(self.info(second), (0, 1, 1))
            self.assertEqual(first.quote(2), 20)
            self.assertEqual(first.evaluations, 2)
            self.assertEqual(second.evaluations, 1)

        def test_business_exceptions_propagate_and_are_not_cached(self):
            for error in (RateUnavailable("down"), TypeError("backend bug"), OSError("I/O")):
                with self.subTest(error=type(error).__name__):
                    state = [error]
                    def backend():
                        if state[0] is not None:
                            raise state[0]
                        return 8
                    account = Account(0, backend)
                    for _ in range(2):
                        with self.assertRaises(type(error)) as found:
                            account.quote(3)
                        self.assertIs(found.exception, error)
                    self.assertEqual(self.info(account), (0, 2, 0))
                    state[0] = None
                    self.assertEqual(account.quote(3), 24)
                    self.assertEqual(self.info(account), (0, 3, 1))

        def test_business_input_errors_not_replaced_by_cache_errors(self):
            for units, fee in ((True, 0), (-1, 0), (1.5, 0), ([], 0),
                               (2, True), (2, -1), (2, [])):
                with self.subTest(units=units, fee=fee):
                    account = Account(10)
                    with self.assertRaises(QuoteError):
                        account.quote(units, fee=fee)
                    self.assertEqual(self.info(account), (0, 1, 0))

        def test_invalid_call_shape_is_not_silently_accepted(self):
            account = Account(10)
            for call in (lambda: account.quote(),
                         lambda: Account.quote(account=account),
                         lambda: account.quote(2, units=2),
                         lambda: account.quote(2, unexpected=True)):
                with self.subTest(call=call), self.assertRaises(TypeError):
                    call()
            self.assertEqual(self.info(account)[2], 0)

        def test_distinct_business_parameters_have_distinct_entries(self):
            account = Account(10)
            self.assertEqual(account.quote(2), 20)
            self.assertEqual(account.quote(2, fee=5), 25)
            self.assertEqual(account.quote(units=2, fee=5), 25)
            self.assertEqual(self.info(account), (1, 2, 2))
            self.assertEqual(account.evaluations, 2)
''')

CASE = {
    "id": "d09", "family": "method-decorator-public-call-compatibility",
    "task": (
        "修复 memo.py 的 memoized_method，让 Account.quote 的合法Python调用形式保持兼容。"
        "原业务方法的接收者参数名为account（不是固定的self）；既要支持绑定调用，也要支持类上显式传实例的调用，"
        "包括接收者按原参数名使用关键字传入、业务参数使用位置或关键字传入。"
        "同一实例、相同业务参数（含省略或显式给出的默认fee=0）应共用缓存，不因合法传参形式不同而重复执行业务方法。"
        "不同实例的缓存、hits、misses必须隔离。公开的Account.quote.cache_info(account)返回含hits、misses、size的统计；"
        "hits为命中返回次数，misses为未命中且尝试执行原业务方法的次数（包括业务异常）；异常不能缓存。"
        "cache_clear(account)只清空该实例的缓存和上述统计，不修改业务执行计数evaluations。"
        "保留公开__wrapped__为可按原签名调用的原业务方法：它应绕过缓存与缓存统计，每次都执行原业务逻辑。"
        "units和fee为严格非负整数（bool不接受）；这些是原业务方法的校验，须仍抛QuoteError，"
        "即使传入不可哈希的非法业务值也不能被缓存键的TypeError覆盖。"
        "后端返回单价或抛异常；RateUnavailable和其他业务异常须按原异常对象传播。"
        "正常缓存命中不调用后端；如后端数据需要刷新，调用者使用cache_clear。"
        "Python参数绑定不合法的调用应继续抛TypeError，不能悄悄接受。"
        "不要求特定实现、语法、内部缓存布局或行数；无需新增框架或改写业务定价。"
    ),
    "files": {"memo.py": INITIAL_MEMO, "accounting.py": ACCOUNTING},
    "reference_files": {"memo.py": REFERENCE_MEMO, "accounting.py": ACCOUNTING},
    "hidden_tests": {"test_contract.py": TESTS},
    "critical": True,
    "checks": ["绑定和类上显式实例的合法位置/关键字形式", "等价参数共用缓存及每实例统计隔离",
               "公开__wrapped__绕过缓存和缓存统计", "异常不缓存且原样传播", "原业务输入错误不被缓存机制替换"],
}


def write_files(directory, files):
    directory.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        destination = directory / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(data, encoding="utf-8")


def main():
    path = HERE / "d09.json"
    path.write_text(json.dumps(CASE, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary = HERE / ".preflight-work" / "tmp"
    temporary.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8",
               TMP=str(temporary), TEMP=str(temporary), TMPDIR=str(temporary))
    phases = {}
    for optimized in (False, True):
        for phase, files in (("initial", CASE["files"]), ("reference", CASE["reference_files"])):
            label = ("optimized_" if optimized else "") + phase
            directory = HERE / ".preflight-work" / label
            write_files(directory, files)
            write_files(directory, CASE["hidden_tests"])
            command = [sys.executable, "-B"] + (["-O"] if optimized else [])
            command += ["-m", "unittest", "discover", "-s", ".", "-p", "test_contract.py", "-v"]
            start = time.monotonic()
            result = subprocess.run(command, cwd=directory, env=env, capture_output=True,
                                    text=True, encoding="utf-8", timeout=30)
            output = result.stdout + result.stderr
            (directory / "unittest.log").write_text(output, encoding="utf-8")
            count = re.search(r"Ran (\d+) tests? in", output)
            failed = re.search(r"failures=(\d+)", output)
            errors = re.search(r"errors=(\d+)", output)
            phases[label] = {"exit_code": result.returncode, "tests": int(count.group(1)) if count else 0,
                             "failures": int(failed.group(1)) if failed else 0,
                             "errors": int(errors.group(1)) if errors else 0,
                             "seconds": round(time.monotonic() - start, 4),
                             "log_sha256": hashlib.sha256(output.encode("utf-8")).hexdigest().upper()}
    eligible = all(phases[prefix + "initial"]["exit_code"] != 0
                   and phases[prefix + "reference"]["exit_code"] == 0
                   and phases[prefix + "initial"]["tests"] == phases[prefix + "reference"]["tests"] == 15
                   for prefix in ("", "optimized_"))
    report = {"checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "case_id": "d09", "case_sha256": hashlib.sha256(path.read_bytes()).hexdigest().upper(),
              "evidence_class": "development_regression_only", "phases": phases,
              "eligible": eligible, "model_calls": 0, "network_calls": 0,
              "candidate_skill_or_solver_results_read": False,
              "no_current_frozen_experiment_modified": True,
              "limit": "Small synthetic stdlib case; no claim of production holdout or field reliability."}
    (HERE / "d09-preflight.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"case_sha256": report["case_sha256"], "eligible": eligible, "phases": phases}))
    assert eligible


if __name__ == "__main__":
    main()
