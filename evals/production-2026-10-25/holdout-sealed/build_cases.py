"""Author sealed production-maintenance fixtures; no model calls."""
from pathlib import Path
import hashlib
import json
import os
import re
import subprocess
import sys
import textwrap
import time

HERE = Path(__file__).resolve().parent
CASES = []


def clean(source):
    return textwrap.dedent(source).strip() + "\n"


def add(number, family, task, shared, initial, reference, tests, checks):
    files = {name: clean(value) for name, value in shared.items()}
    files["app.py"] = clean(initial)
    ref = dict(files)
    ref["app.py"] = clean(reference)
    CASES.append({"id": f"h{number:02}", "family": family, "task": task,
                  "files": files, "reference_files": ref,
                  "hidden_tests": {"test_contract.py": clean(tests)},
                  "critical": True, "checks": checks})


add(1, "parse-and-single-validation",
    "仓库导入接口 import_inventory 接收 JSON 数组；每项 sku 为非空字符串，quantity 为严格正整数（bool 不接受），重复 sku 拒绝。空数组合法。解析或字段问题抛 InventoryError。调用方可以注入同契约的可信内部 validator：将数据转换为已验证的不可变tuple[Row]。每次导入只能调用它一次，因为生产环境验证包含昂贵查表。保留返回的不可变 Manifest 及其行顺序，导入不能修改传入 validator 接收到的数据。修复 app.py 的导入流程并保留这些边界行为。",
    {"domain.py": '''
        from dataclasses import dataclass

        class InventoryError(ValueError):
            pass

        @dataclass(frozen=True)
        class Row:
            sku: str
            quantity: int

        @dataclass(frozen=True)
        class Manifest:
            rows: tuple
            total: int

        def validate_rows(payload):
            if not isinstance(payload, (list, tuple)):
                raise InventoryError("expected rows")
            result, seen = [], set()
            for item in payload:
                if isinstance(item, Row):
                    sku, quantity = item.sku, item.quantity
                elif isinstance(item, dict):
                    sku, quantity = item.get("sku"), item.get("quantity")
                else:
                    raise InventoryError("expected row")
                if not isinstance(sku, str) or not sku.strip():
                    raise InventoryError("sku")
                if type(quantity) is not int or quantity <= 0:
                    raise InventoryError("quantity")
                if sku in seen:
                    raise InventoryError("duplicate sku")
                seen.add(sku)
                result.append(Row(sku, quantity))
            return tuple(result)
    '''},
    '''
        import json
        from domain import InventoryError, Manifest, validate_rows

        def _render(rows, validator):
            checked = validator(rows)
            return Manifest(checked, sum(row.quantity for row in checked))

        def import_inventory(text, validator=validate_rows):
            """Parse external inventory and produce an immutable manifest."""
            try:
                payload = json.loads(text)
            except (ValueError, TypeError) as exc:
                raise InventoryError("invalid JSON") from exc
            checked = validator(payload)
            return _render(checked, validator)
    ''',
    '''
        import json
        from domain import InventoryError, Manifest, validate_rows

        def _render(rows):
            return Manifest(rows, sum(row.quantity for row in rows))

        def import_inventory(text, validator=validate_rows):
            """Validate the external batch once; own the resulting rows."""
            try:
                payload = json.loads(text)
            except (ValueError, TypeError) as exc:
                raise InventoryError("invalid JSON") from exc
            return _render(validator(payload))
    ''',
    '''
        import dataclasses
        import json
        import unittest
        from app import import_inventory
        from domain import InventoryError, Row, validate_rows

        class Contract(unittest.TestCase):
            def valid(self):
                return '[{"sku":"A","quantity":2},{"sku":"B","quantity":3}]'

            def test_values_and_order(self):
                value = import_inventory(self.valid())
                self.assertEqual(value.rows, (Row("A", 2), Row("B", 3)))
                self.assertEqual(value.total, 5)

            def test_validator_called_once(self):
                calls = []
                def validator(value):
                    calls.append(value)
                    return validate_rows(value)
                self.assertEqual(import_inventory(self.valid(), validator).total, 5)
                self.assertEqual(len(calls), 1)

            def test_validator_input_not_mutated(self):
                captured = []
                def validator(value):
                    if not captured:
                        captured.append(value)
                    return validate_rows(value)
                import_inventory(self.valid(), validator)
                self.assertEqual(captured[0], json.loads(self.valid()))

            def test_empty(self):
                self.assertEqual(import_inventory("[]").rows, ())

            def test_invalid_json(self):
                with self.assertRaises(InventoryError):
                    import_inventory("{")

            def test_non_array(self):
                with self.assertRaises(InventoryError):
                    import_inventory('{"sku":"A"}')

            def test_quantities(self):
                for quantity in (True, 0, -1, "2", 1.5):
                    with self.subTest(quantity=quantity), self.assertRaises(InventoryError):
                        import_inventory(json.dumps([{"sku": "A", "quantity": quantity}]))

            def test_bad_sku(self):
                for sku in (None, "", "  ", 3):
                    with self.subTest(sku=sku), self.assertRaises(InventoryError):
                        import_inventory(json.dumps([{"sku": sku, "quantity": 1}]))

            def test_duplicate(self):
                with self.assertRaises(InventoryError):
                    import_inventory('[{"sku":"A","quantity":1},{"sku":"A","quantity":2}]')

            def test_owned_immutable_result(self):
                value = import_inventory(self.valid())
                with self.assertRaises(dataclasses.FrozenInstanceError):
                    value.rows[0].quantity = 100
    ''',
    ["JSON与字段验证", "昂贵validator仅一次", "顺序和不可变结果", "重复sku与bool拒绝"])

add(2, "trusted-and-bypass-paths",
    "订单报价有 quote_web 与 quote_partner 两条路径。网页路径先通过 parse_cart，伙伴路径可以直接提交 Cart；仅有类型注解或 Cart 实例并不表示校验过。两条公开路径都必须拒绝负单价、非严格正整数数量和不在0到100之间的严格整数折扣（bool拒绝），抛 CartError。返回整数分：unit_cents * quantity * (100-discount) // 100。网页parser只调用一次；注入parser是同契约的可信内部替换，返回已经验证的不可变Cart。伙伴路径保留运行时校验；调用不能改输入。",
    {"domain.py": '''
        from dataclasses import dataclass

        class CartError(ValueError):
            pass

        @dataclass(frozen=True)
        class Cart:
            unit_cents: int
            quantity: int
            discount: int = 0

        def validate_cart(cart):
            if not isinstance(cart, Cart):
                raise CartError("cart")
            if type(cart.unit_cents) is not int or cart.unit_cents < 0:
                raise CartError("unit price")
            if type(cart.quantity) is not int or cart.quantity <= 0:
                raise CartError("quantity")
            if type(cart.discount) is not int or not 0 <= cart.discount <= 100:
                raise CartError("discount")
            return cart

        def parse_cart(payload):
            if not isinstance(payload, dict):
                raise CartError("payload")
            try:
                cart = Cart(payload["unit_cents"], payload["quantity"], payload.get("discount", 0))
            except (KeyError, TypeError) as exc:
                raise CartError("fields") from exc
            return validate_cart(cart)
    '''},
    '''
        from domain import parse_cart, validate_cart

        def _price(cart):
            return cart.unit_cents * cart.quantity * (100 - cart.discount) // 100

        def quote_web(payload, parser=parse_cart):
            """The parser owns runtime validation on this route."""
            return _price(parser(payload))

        def quote_partner(cart):
            """Partners can construct Cart objects outside our parser."""
            return _price(cart)
    ''',
    '''
        from domain import parse_cart, validate_cart

        def _price(cart):
            return cart.unit_cents * cart.quantity * (100 - cart.discount) // 100

        def quote_web(payload, parser=parse_cart):
            """The parser owns runtime validation on this route."""
            return _price(parser(payload))

        def quote_partner(cart):
            """The bypass route validates before entering the trusted helper."""
            return _price(validate_cart(cart))
    ''',
    '''
        import unittest
        from app import quote_web, quote_partner
        from domain import Cart, CartError, parse_cart

        class Contract(unittest.TestCase):
            def test_web_price(self):
                self.assertEqual(quote_web({"unit_cents": 199, "quantity": 3, "discount": 10}), 537)

            def test_partner_price(self):
                self.assertEqual(quote_partner(Cart(250, 2, 20)), 400)

            def test_partner_negative_price(self):
                with self.assertRaises(CartError):
                    quote_partner(Cart(-1, 2))

            def test_partner_bad_quantity(self):
                for q in (True, 0, -1, 1.5):
                    with self.subTest(q=q), self.assertRaises(CartError):
                        quote_partner(Cart(10, q))

            def test_partner_bad_discount(self):
                for d in (True, -1, 101, "10"):
                    with self.subTest(d=d), self.assertRaises(CartError):
                        quote_partner(Cart(100, 1, d))

            def test_web_invalid(self):
                with self.assertRaises(CartError):
                    quote_web({"unit_cents": 10, "quantity": False})

            def test_web_missing_fields(self):
                with self.assertRaises(CartError):
                    quote_web({"quantity": 1})

            def test_web_parser_once(self):
                calls = []
                def parser(payload):
                    calls.append(payload)
                    return parse_cart(payload)
                self.assertEqual(quote_web({"unit_cents": 5, "quantity": 2}, parser), 10)
                self.assertEqual(len(calls), 1)

            def test_input_unchanged(self):
                payload = {"unit_cents": 10, "quantity": 2}
                quote_web(payload)
                self.assertEqual(payload, {"unit_cents": 10, "quantity": 2})

            def test_boundary_prices(self):
                self.assertEqual(quote_partner(Cart(0, 1)), 0)
                self.assertEqual(quote_partner(Cart(100, 1, 100)), 0)
    ''',
    ["两条公开路径覆盖", "旁路不可凭类型免检", "可信路径parser一次", "整数金额与边界"])

add(3, "owned-snapshot",
    "购物车 prepare_quote(cart, catalog) 应生成可以稍后结算的不可变报价快照：每个 line 保存 sku、当时单价和数量，total 为整数分。之后 cart（包括内部字典）或 catalog 的变动不能改变已生成的报价，也不能通过报价修改调用者数据。sku须存在；数量为严格正整数；单价为非负严格整数；非法输入抛 QuoteError。保留条目顺序，空车合法。请修复快照所有权。",
    {"domain.py": '''
        from dataclasses import dataclass

        class QuoteError(ValueError):
            pass

        @dataclass(frozen=True)
        class Line:
            sku: str
            unit_cents: int
            quantity: int

        @dataclass(frozen=True)
        class Quote:
            lines: tuple
            total: int

        def check(cart, catalog):
            if not isinstance(cart, list) or not isinstance(catalog, dict):
                raise QuoteError("container")
            for row in cart:
                if not isinstance(row, dict):
                    raise QuoteError("row")
                sku, quantity = row.get("sku"), row.get("quantity")
                if not isinstance(sku, str) or sku not in catalog:
                    raise QuoteError("sku")
                if type(quantity) is not int or quantity <= 0:
                    raise QuoteError("quantity")
                price = catalog[sku]
                if type(price) is not int or price < 0:
                    raise QuoteError("price")
    '''},
    '''
        from domain import Line, Quote, QuoteError, check

        class LiveQuote:
            """Legacy view; callers currently keep this for delayed settlement."""
            def __init__(self, cart, catalog):
                self.cart = cart
                self.catalog = catalog

            @property
            def lines(self):
                return tuple(Line(row["sku"], self.catalog[row["sku"]], row["quantity"]) for row in self.cart)

            @property
            def total(self):
                return sum(line.unit_cents * line.quantity for line in self.lines)

        def prepare_quote(cart, catalog):
            check(cart, catalog)
            return LiveQuote(cart, catalog)
    ''',
    '''
        from domain import Line, Quote, QuoteError, check

        def prepare_quote(cart, catalog):
            check(cart, catalog)
            lines = tuple(Line(row["sku"], catalog[row["sku"]], row["quantity"]) for row in cart)
            return Quote(lines, sum(line.unit_cents * line.quantity for line in lines))
    ''',
    '''
        import dataclasses
        import unittest
        from app import prepare_quote
        from domain import Line, QuoteError

        class Contract(unittest.TestCase):
            def cart(self):
                return [{"sku": "A", "quantity": 2}, {"sku": "B", "quantity": 1}]

            def test_values(self):
                quote = prepare_quote(self.cart(), {"A": 10, "B": 7})
                self.assertEqual(quote.lines, (Line("A", 10, 2), Line("B", 7, 1)))
                self.assertEqual(quote.total, 27)

            def test_cart_alias(self):
                cart = self.cart()
                quote = prepare_quote(cart, {"A": 10, "B": 7})
                cart[0]["quantity"] = 99
                cart.clear()
                self.assertEqual(quote.total, 27)

            def test_catalog_alias(self):
                catalog = {"A": 10, "B": 7}
                quote = prepare_quote(self.cart(), catalog)
                catalog["A"] = 999
                catalog.clear()
                self.assertEqual(quote.lines[0], Line("A", 10, 2))

            def test_frozen(self):
                quote = prepare_quote(self.cart(), {"A": 10, "B": 7})
                with self.assertRaises((AttributeError, dataclasses.FrozenInstanceError)):
                    quote.total = 2

            def test_input_not_changed(self):
                cart, catalog = self.cart(), {"A": 10, "B": 7}
                prepare_quote(cart, catalog)
                self.assertEqual(cart, self.cart())
                self.assertEqual(catalog, {"A": 10, "B": 7})

            def test_empty(self):
                quote = prepare_quote([], {})
                self.assertEqual((quote.lines, quote.total), ((), 0))

            def test_missing_sku(self):
                with self.assertRaises(QuoteError):
                    prepare_quote(self.cart(), {"A": 10})

            def test_quantities(self):
                for value in (0, -1, True, "1"):
                    with self.subTest(value=value), self.assertRaises(QuoteError):
                        prepare_quote([{"sku": "A", "quantity": value}], {"A": 10})

            def test_prices(self):
                for value in (-1, True, 1.5):
                    with self.subTest(value=value), self.assertRaises(QuoteError):
                        prepare_quote([{"sku": "A", "quantity": 1}], {"A": value})

            def test_bad_container(self):
                with self.assertRaises(QuoteError):
                    prepare_quote(({"sku": "A", "quantity": 1},), {"A": 10})
    ''',
    ["延迟结算独立快照", "嵌套别名与冻结结果", "输入非变异", "数量价格边界"])

add(4, "cache-error-semantics",
    "产品缓存 ProductCache.get(key, force=False) 使用ttl秒和注入时钟。新鲜命中不查库；None是正常不存在结果也可缓存；超时或force要刷新，刷新开始即删除旧条目，失败不能返回旧值或None，也不能缓存失败。ttl必须为有限正数。API.lookup只将 RepositoryError 翻译为 ServiceUnavailable并保留cause，其他异常原样传播。保留 invalidate 和按key隔离。修复当前缓存/API的错误语义。",
    {"cache.py": '''
        import time
        import math

        class RepositoryError(RuntimeError):
            pass

        class ServiceUnavailable(RuntimeError):
            pass

        class ProductCache:
            def __init__(self, repository, ttl=10, clock=time.monotonic):
                if isinstance(ttl, bool) or not isinstance(ttl, (int, float)) or ttl <= 0 or not math.isfinite(ttl):
                    raise ValueError("ttl")
                self.repository = repository
                self.ttl = ttl
                self.clock = clock
                self.entries = {}

            def invalidate(self, key):
                self.entries.pop(key, None)

            def get(self, key, force=False):
                now = self.clock()
                if not force and key in self.entries:
                    expires, value = self.entries[key]
                    if now < expires:
                        return value
                old = self.entries.get(key)
                try:
                    value = self.repository(key)
                except Exception:
                    return old[1] if old else None
                self.entries[key] = (now + self.ttl, value)
                return value
    '''},
    '''
        from cache import RepositoryError, ServiceUnavailable

        class API:
            def __init__(self, cache):
                self.cache = cache

            def lookup(self, key, force=False):
                try:
                    return self.cache.get(key, force=force)
                except Exception as exc:
                    raise ServiceUnavailable("unavailable") from exc
    ''',
    '''
        from cache import RepositoryError, ServiceUnavailable

        class API:
            def __init__(self, cache):
                self.cache = cache

            def lookup(self, key, force=False):
                try:
                    return self.cache.get(key, force=force)
                except RepositoryError as exc:
                    raise ServiceUnavailable("unavailable") from exc
    ''',
    '''
        import unittest
        from app import API
        from cache import ProductCache, RepositoryError, ServiceUnavailable

        class Repository:
            def __init__(self):
                self.calls = []
                self.value = "first"
                self.error = None
            def __call__(self, key):
                self.calls.append(key)
                if self.error:
                    raise self.error
                return self.value

        class Contract(unittest.TestCase):
            def setUp(self):
                self.now = [0]
                self.repo = Repository()
                self.cache = ProductCache(self.repo, 10, lambda: self.now[0])

            def test_fresh_hit(self):
                self.assertEqual(self.cache.get("A"), "first")
                self.repo.value = "second"
                self.assertEqual(self.cache.get("A"), "first")
                self.assertEqual(self.repo.calls, ["A"])

            def test_expiry(self):
                self.cache.get("A")
                self.now[0] = 10
                self.repo.value = "new"
                self.assertEqual(self.cache.get("A"), "new")

            def test_none_is_normal(self):
                self.repo.value = None
                self.assertIsNone(self.cache.get("A"))
                self.assertIsNone(self.cache.get("A"))
                self.assertEqual(self.repo.calls, ["A"])

            def test_force_failure_evicts_stale(self):
                self.cache.get("A")
                self.repo.error = RepositoryError("down")
                with self.assertRaises(RepositoryError):
                    self.cache.get("A", force=True)
                self.assertNotIn("A", self.cache.entries)

            def test_failed_miss_not_cached(self):
                error = RepositoryError("down")
                self.repo.error = error
                with self.assertRaises(RepositoryError) as found:
                    self.cache.get("A")
                self.assertIs(found.exception, error)
                self.assertEqual(self.cache.entries, {})

            def test_expected_boundary_translation(self):
                error = RepositoryError("down")
                self.repo.error = error
                with self.assertRaises(ServiceUnavailable) as found:
                    API(self.cache).lookup("A")
                self.assertIs(found.exception.__cause__, error)

            def test_unexpected_propagates(self):
                error = ValueError("bug")
                self.repo.error = error
                with self.assertRaises(ValueError) as found:
                    API(self.cache).lookup("A")
                self.assertIs(found.exception, error)

            def test_invalidate(self):
                self.cache.get("A")
                self.cache.invalidate("A")
                self.repo.value = "new"
                self.assertEqual(self.cache.get("A"), "new")

            def test_key_isolation(self):
                self.cache.get("A")
                self.repo.value = "B"
                self.assertEqual(self.cache.get("B"), "B")
                self.assertEqual(self.cache.get("A"), "first")

            def test_invalid_ttl(self):
                for value in (0, -1, True, "10", float("nan"), float("inf")):
                    with self.subTest(value=value), self.assertRaises(ValueError):
                        ProductCache(self.repo, value)
    ''',
    ["命中与TTL", "None正常缺失", "刷新失败不可伪装成功", "窄边界翻译及cause"])

# Case 4's cache is a second production file, so its reference replacement is
# added explicitly rather than pretending app.py alone implements the change.
CASES[-1]["reference_files"]["cache.py"] = clean('''
    import time
    import math

    class RepositoryError(RuntimeError):
        pass

    class ServiceUnavailable(RuntimeError):
        pass

    class ProductCache:
        def __init__(self, repository, ttl=10, clock=time.monotonic):
            if isinstance(ttl, bool) or not isinstance(ttl, (int, float)) or ttl <= 0 or not math.isfinite(ttl):
                raise ValueError("ttl")
            self.repository = repository
            self.ttl = ttl
            self.clock = clock
            self.entries = {}

        def invalidate(self, key):
            self.entries.pop(key, None)

        def get(self, key, force=False):
            now = self.clock()
            if not force and key in self.entries:
                expires, value = self.entries[key]
                if now < expires:
                    return value
            self.entries.pop(key, None)
            value = self.repository(key)
            self.entries[key] = (now + self.ttl, value)
            return value
''')

add(5, "bounded-idempotent-retry",
    "开票请求 create_invoice 必须携带调用者给定的非空幂等key（字符串），同一请求的全部尝试使用同一key，amount为严格正整数分。网关只保证 TransientError 可重试；DeclinedError和其他异常原样传播，不重试。max_attempts含首次请求，必须是1到5的严格整数；耗尽后传播最后原异常。重试前注入sleep按0.01、0.02、0.04…秒调用，成功不额外等待。修复重试逻辑，不能创建新幂等key或吞掉异常。",
    {"gateway.py": '''
        class TransientError(ConnectionError):
            """The gateway supports retrying this with a stable idempotency key."""
            pass

        class DeclinedError(RuntimeError):
            """The request was rejected; repeating it is not recovery."""
            pass

        def validate_request(amount, key):
            if type(amount) is not int or amount <= 0:
                raise ValueError("amount")
            if not isinstance(key, str) or not key.strip():
                raise ValueError("key")

        def validate_attempts(value):
            if type(value) is not int or not 1 <= value <= 5:
                raise ValueError("max_attempts")

        class InvoiceRequest:
            """A validated request owned by this invocation.

            The supplied key identifies the logical invoice across attempts.
            """
            def __init__(self, amount, key):
                validate_request(amount, key)
                self.amount = amount
                self.key = key
    '''},
    '''
        import time
        import uuid
        from gateway import TransientError, DeclinedError, InvoiceRequest, validate_attempts

        def create_invoice(transport, amount, key, max_attempts=3, sleep=time.sleep):
            request = InvoiceRequest(amount, key)
            validate_attempts(max_attempts)
            last_error = None
            for attempt in range(max_attempts):
                try:
                    return transport(request.amount, str(uuid.uuid4()))
                except Exception as exc:
                    last_error = exc
                    if attempt + 1 < max_attempts:
                        sleep(0.01 * (2 ** attempt))
            raise last_error
    ''',
    '''
        import time
        from gateway import TransientError, DeclinedError, InvoiceRequest, validate_attempts

        def create_invoice(transport, amount, key, max_attempts=3, sleep=time.sleep):
            request = InvoiceRequest(amount, key)
            validate_attempts(max_attempts)
            for attempt in range(max_attempts):
                try:
                    return transport(request.amount, request.key)
                except TransientError:
                    if attempt + 1 == max_attempts:
                        raise
                    sleep(0.01 * (2 ** attempt))
    ''',
    '''
        import unittest
        from app import create_invoice
        from gateway import TransientError, DeclinedError

        class Transport:
            def __init__(self, outcomes):
                self.outcomes, self.calls = list(outcomes), []
            def __call__(self, amount, key):
                self.calls.append((amount, key))
                outcome = self.outcomes.pop(0)
                if isinstance(outcome, BaseException):
                    raise outcome
                return outcome

        class Contract(unittest.TestCase):
            def test_success_without_wait(self):
                transport, waits = Transport(["receipt"]), []
                self.assertEqual(create_invoice(transport, 100, "k", sleep=waits.append), "receipt")
                self.assertEqual(waits, [])

            def test_stable_key(self):
                transport = Transport([TransientError("once"), "ok"])
                create_invoice(transport, 100, "caller-key", sleep=lambda _: None)
                self.assertEqual(transport.calls, [(100, "caller-key"), (100, "caller-key")])

            def test_two_retries(self):
                transport, waits = Transport([TransientError(), TransientError(), "ok"]), []
                self.assertEqual(create_invoice(transport, 1, "k", sleep=waits.append), "ok")
                self.assertEqual(waits, [0.01, 0.02])

            def test_decline_not_retried(self):
                error = DeclinedError("no")
                transport = Transport([error, "wrong"])
                with self.assertRaises(DeclinedError) as found:
                    create_invoice(transport, 1, "k", sleep=lambda _: None)
                self.assertIs(found.exception, error)
                self.assertEqual(len(transport.calls), 1)

            def test_bug_not_retried(self):
                error = ValueError("bug")
                transport = Transport([error, "wrong"])
                with self.assertRaises(ValueError) as found:
                    create_invoice(transport, 1, "k", sleep=lambda _: None)
                self.assertIs(found.exception, error)
                self.assertEqual(len(transport.calls), 1)

            def test_budget_exhaustion(self):
                error = TransientError("last")
                transport, waits = Transport([TransientError(), error]), []
                with self.assertRaises(TransientError) as found:
                    create_invoice(transport, 1, "k", max_attempts=2, sleep=waits.append)
                self.assertIs(found.exception, error)
                self.assertEqual(len(transport.calls), 2)
                self.assertEqual(waits, [0.01])

            def test_one_attempt(self):
                transport, waits = Transport([TransientError("only")]), []
                with self.assertRaises(TransientError):
                    create_invoice(transport, 1, "k", max_attempts=1, sleep=waits.append)
                self.assertEqual(waits, [])

            def test_missing_key_before_io(self):
                for key in (None, "", " ", 3):
                    transport = Transport(["bad"])
                    with self.subTest(key=key), self.assertRaises(ValueError):
                        create_invoice(transport, 1, key)
                    self.assertEqual(transport.calls, [])

            def test_invalid_amount(self):
                for amount in (True, 0, -1, 1.2):
                    with self.subTest(amount=amount), self.assertRaises(ValueError):
                        create_invoice(Transport(["bad"]), amount, "k")

            def test_invalid_budget(self):
                for budget in (True, 0, 6, 1.5):
                    with self.subTest(budget=budget), self.assertRaises(ValueError):
                        create_invoice(Transport(["bad"]), 1, "k", max_attempts=budget)
    ''',
    ["稳定幂等key", "只重试已知瞬态", "次数与退避有界", "业务拒绝和未知异常传播"])

add(6, "nonrepeatable-side-effect",
    "打印派发 dispatch(store, send, job_id, payload) 的外部send不支持幂等。store负责对job_id原子占位：进行中抛 InProgress，已成功且payload相同返回原receipt，不再次send；已存在但payload不同抛ValueError。只有NotAccepted保证未受理，应释放占位并原样传播，后续显式调用可以再试。DeliveryUnknown或其他未分类异常都不能排除已打印，必须标记unknown并原样传播；之后同job_id调用抛NeedsReconciliation且不能重发。每次dispatch最多调用send一次。job_id、payload为非空字符串。修复服务，保留并发占位与不同job独立性。",
    {"jobs.py": '''
        from dataclasses import dataclass
        from threading import Lock

        class DeliveryUnknown(RuntimeError):
            pass
        class NotAccepted(ConnectionError):
            pass
        class NeedsReconciliation(RuntimeError):
            pass
        class InProgress(RuntimeError):
            pass

        @dataclass(frozen=True)
        class Record:
            payload: str
            state: str
            receipt: object = None

        class Store:
            def __init__(self):
                self.rows = {}
                self.lock = Lock()

            def claim(self, job_id, payload):
                with self.lock:
                    old = self.rows.get(job_id)
                    if old is not None:
                        if old.payload != payload:
                            raise ValueError("payload conflict")
                        if old.state == "sent":
                            return old
                        if old.state == "unknown":
                            raise NeedsReconciliation(job_id)
                        raise InProgress(job_id)
                    self.rows[job_id] = Record(payload, "pending")
                    return None

            def finish(self, job_id, payload, receipt):
                with self.lock:
                    self.rows[job_id] = Record(payload, "sent", receipt)

            def unknown(self, job_id, payload):
                with self.lock:
                    self.rows[job_id] = Record(payload, "unknown")

            def release(self, job_id):
                with self.lock:
                    self.rows.pop(job_id, None)
    '''},
    '''
        from jobs import DeliveryUnknown, NotAccepted, NeedsReconciliation, InProgress

        def dispatch(store, send, job_id, payload):
            if not isinstance(job_id, str) or not job_id.strip():
                raise ValueError("job_id")
            if not isinstance(payload, str) or not payload.strip():
                raise ValueError("payload")
            old = store.claim(job_id, payload)
            if old is not None:
                return send(payload)
            for attempt in range(2):
                try:
                    receipt = send(payload)
                    store.finish(job_id, payload, receipt)
                    return receipt
                except Exception:
                    if attempt == 1:
                        store.release(job_id)
                        raise
    ''',
    '''
        from jobs import DeliveryUnknown, NotAccepted, NeedsReconciliation, InProgress

        def dispatch(store, send, job_id, payload):
            if not isinstance(job_id, str) or not job_id.strip():
                raise ValueError("job_id")
            if not isinstance(payload, str) or not payload.strip():
                raise ValueError("payload")
            old = store.claim(job_id, payload)
            if old is not None:
                return old.receipt
            try:
                receipt = send(payload)
            except NotAccepted:
                store.release(job_id)
                raise
            except BaseException:
                store.unknown(job_id, payload)
                raise
            store.finish(job_id, payload, receipt)
            return receipt
    ''',
    '''
        import threading
        import unittest
        from app import dispatch
        from jobs import Store, DeliveryUnknown, NotAccepted, NeedsReconciliation, InProgress

        class Contract(unittest.TestCase):
            def setUp(self):
                self.store = Store()
                self.calls = []

            def send(self, payload):
                self.calls.append(payload)
                return "receipt-1"

            def test_success(self):
                self.assertEqual(dispatch(self.store, self.send, "j", "doc"), "receipt-1")
                self.assertEqual(self.store.rows["j"].state, "sent")

            def test_success_duplicate(self):
                first = dispatch(self.store, self.send, "j", "doc")
                self.assertEqual(dispatch(self.store, self.send, "j", "doc"), first)
                self.assertEqual(self.calls, ["doc"])

            def test_unknown_never_retried(self):
                error = DeliveryUnknown("response lost")
                def send(payload):
                    self.calls.append(payload)
                    raise error
                with self.assertRaises(DeliveryUnknown) as found:
                    dispatch(self.store, send, "j", "doc")
                self.assertIs(found.exception, error)
                self.assertEqual(self.calls, ["doc"])
                self.assertEqual(self.store.rows["j"].state, "unknown")

            def test_unknown_requires_reconciliation(self):
                self.store.claim("j", "doc")
                self.store.unknown("j", "doc")
                with self.assertRaises(NeedsReconciliation):
                    dispatch(self.store, self.send, "j", "doc")
                self.assertEqual(self.calls, [])

            def test_definite_failure_once_and_release(self):
                error = NotAccepted("before acceptance")
                def send(payload):
                    self.calls.append(payload)
                    raise error
                with self.assertRaises(NotAccepted) as found:
                    dispatch(self.store, send, "j", "doc")
                self.assertIs(found.exception, error)
                self.assertEqual(self.calls, ["doc"])
                self.assertNotIn("j", self.store.rows)

            def test_explicit_retry_after_rejection(self):
                def fail(payload):
                    raise NotAccepted()
                with self.assertRaises(NotAccepted):
                    dispatch(self.store, fail, "j", "doc")
                self.assertEqual(dispatch(self.store, self.send, "j", "doc"), "receipt-1")

            def test_unclassified_failure_is_uncertain(self):
                error = TimeoutError("acceptance unknown")
                def fail(payload):
                    self.calls.append(payload)
                    raise error
                with self.assertRaises(TimeoutError) as found:
                    dispatch(self.store, fail, "j", "doc")
                self.assertIs(found.exception, error)
                self.assertEqual(self.calls, ["doc"])
                self.assertEqual(self.store.rows["j"].state, "unknown")
                with self.assertRaises(NeedsReconciliation):
                    dispatch(self.store, self.send, "j", "doc")

            def test_payload_conflict(self):
                dispatch(self.store, self.send, "j", "doc")
                with self.assertRaises(ValueError):
                    dispatch(self.store, self.send, "j", "different")
                self.assertEqual(self.calls, ["doc"])

            def test_pending_is_not_resent(self):
                self.store.claim("j", "doc")
                with self.assertRaises(InProgress):
                    dispatch(self.store, self.send, "j", "doc")
                self.assertEqual(self.calls, [])

            def test_different_jobs(self):
                dispatch(self.store, self.send, "j1", "doc")
                dispatch(self.store, self.send, "j2", "doc")
                self.assertEqual(self.calls, ["doc", "doc"])

            def test_invalid_input_before_send(self):
                for job, payload in (("", "x"), (None, "x"), ("j", ""), ("j", 3)):
                    with self.subTest(job=job, payload=payload), self.assertRaises(ValueError):
                        dispatch(self.store, self.send, job, payload)
                self.assertEqual(self.calls, [])
    ''',
    ["不支持幂等的副作用不自动重试", "unknown需对账", "成功去重与payload冲突", "占位和失败释放"])

add(7, "sqlite-transaction-atomicity",
    "修复 transfer(conn, source, destination, cents)：两个账户转账和audit插入必须同一事务全部提交或全部回滚。cents为严格正整数，两个账户不同且都存在；不满足抛TransferError。余额不足抛InsufficientFunds；SQLite异常保留原类型传播。连接归调用者所有，不能关闭。若调用前conn已有活动事务，直接抛TransferError且不能改动或回滚调用者事务。其他情形函数成功返回后提交、失败后回滚并仍能继续使用连接。",
    {"db.py": '''
        import sqlite3

        class TransferError(ValueError):
            pass

        class InsufficientFunds(TransferError):
            pass

        def new_database():
            """Create the application's small ledger schema.

            The returned connection is owned by the caller, not transfer().
            """
            conn = sqlite3.connect(":memory:")
            conn.executescript("""
                CREATE TABLE accounts (id TEXT PRIMARY KEY, balance INTEGER NOT NULL);
                CREATE TABLE audit (source TEXT, destination TEXT, cents INTEGER);
                INSERT INTO accounts VALUES ('alice', 100), ('bob', 20);
            """)
            return conn

        def balance(conn, account):
            row = conn.execute("SELECT balance FROM accounts WHERE id=?", (account,)).fetchone()
            if row is None:
                raise TransferError("unknown account")
            return row[0]
    '''},
    '''
        from db import TransferError, InsufficientFunds, balance

        def transfer(conn, source, destination, cents):
            if type(cents) is not int or cents <= 0:
                raise TransferError("cents")
            if source == destination:
                raise TransferError("same account")
            if balance(conn, source) < cents:
                raise InsufficientFunds("balance")
            conn.execute("UPDATE accounts SET balance=balance-? WHERE id=?", (cents, source))
            conn.commit()
            balance(conn, destination)
            conn.execute("UPDATE accounts SET balance=balance+? WHERE id=?", (cents, destination))
            conn.execute("INSERT INTO audit VALUES (?,?,?)", (source, destination, cents))
            conn.commit()
    ''',
    '''
        from db import TransferError, InsufficientFunds, balance

        def transfer(conn, source, destination, cents):
            if conn.in_transaction:
                raise TransferError("caller transaction active")
            if type(cents) is not int or cents <= 0:
                raise TransferError("cents")
            if source == destination:
                raise TransferError("same account")
            with conn:
                if balance(conn, source) < cents:
                    raise InsufficientFunds("balance")
                balance(conn, destination)
                conn.execute("UPDATE accounts SET balance=balance-? WHERE id=?", (cents, source))
                conn.execute("UPDATE accounts SET balance=balance+? WHERE id=?", (cents, destination))
                conn.execute("INSERT INTO audit VALUES (?,?,?)", (source, destination, cents))
    ''',
    '''
        import sqlite3
        import unittest
        from app import transfer
        from db import new_database, balance, TransferError, InsufficientFunds

        class Contract(unittest.TestCase):
            def setUp(self):
                self.conn = new_database()
                self.addCleanup(self.conn.close)

            def test_success(self):
                transfer(self.conn, "alice", "bob", 30)
                self.assertEqual((balance(self.conn, "alice"), balance(self.conn, "bob")), (70, 50))
                self.assertEqual(self.conn.execute("SELECT * FROM audit").fetchall(), [("alice", "bob", 30)])

            def test_missing_destination_rolls_back(self):
                with self.assertRaises(TransferError):
                    transfer(self.conn, "alice", "missing", 10)
                self.assertEqual(balance(self.conn, "alice"), 100)
                self.assertFalse(self.conn.in_transaction)

            def test_audit_failure_rolls_back(self):
                self.conn.execute("CREATE TRIGGER no_audit BEFORE INSERT ON audit BEGIN SELECT RAISE(ABORT,'unavailable'); END")
                with self.assertRaises(sqlite3.IntegrityError):
                    transfer(self.conn, "alice", "bob", 10)
                self.assertEqual((balance(self.conn, "alice"), balance(self.conn, "bob")), (100, 20))
                self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM audit").fetchone()[0], 0)

            def test_insufficient(self):
                with self.assertRaises(InsufficientFunds):
                    transfer(self.conn, "alice", "bob", 101)
                self.assertEqual(balance(self.conn, "alice"), 100)

            def test_bad_amounts(self):
                for cents in (-1, 0, True, "5"):
                    with self.subTest(cents=cents), self.assertRaises(TransferError):
                        transfer(self.conn, "alice", "bob", cents)
                self.assertEqual(balance(self.conn, "alice"), 100)

            def test_same_account(self):
                with self.assertRaises(TransferError):
                    transfer(self.conn, "alice", "alice", 1)

            def test_connection_owned_by_caller(self):
                transfer(self.conn, "alice", "bob", 1)
                self.assertEqual(self.conn.execute("SELECT 7").fetchone(), (7,))

            def test_existing_transaction_preserved(self):
                self.conn.execute("UPDATE accounts SET balance=120 WHERE id='alice'")
                with self.assertRaises(TransferError):
                    transfer(self.conn, "alice", "bob", 10)
                self.assertTrue(self.conn.in_transaction)
                self.assertEqual(balance(self.conn, "alice"), 120)
                self.conn.rollback()
                self.assertEqual(balance(self.conn, "alice"), 100)

            def test_missing_source(self):
                with self.assertRaises(TransferError):
                    transfer(self.conn, "missing", "bob", 10)
                self.assertEqual(balance(self.conn, "bob"), 20)

            def test_database_error_propagates(self):
                self.conn.execute("DROP TABLE audit")
                with self.assertRaises(sqlite3.OperationalError):
                    transfer(self.conn, "alice", "bob", 1)
                self.assertEqual(balance(self.conn, "alice"), 100)
    ''',
    ["借贷和审计原子提交", "缺失账户及数据库错误回滚", "调用者连接生命周期", "不破坏外层事务"])

add(8, "resource-and-atomic-file-lifecycle",
    "download(opener, target, replace=os.replace) 将资源完整下载后原子替换target；返回True。opener若抛AssetMissing表示正常不存在，返回False且不动目标；其他异常传播。response接口约定read(size)返回bytes，b''表示EOF；response有close且归本函数所有，read和close都可能失败。所有路径必须close恰好一次（opener失败除外），部分数据不能覆盖旧文件，临时文件必须清理。close失败时也不能替换目标；若读取已失败，close的普通异常不能覆盖原读取异常。target是有效路径字符串或Path；本函数不自动创建父目录，父目录不可用时传播创建异常并关闭响应。修复下载资源和文件生命周期。",
    {"errors.py": '''
        from typing import Callable, Protocol

        class AssetMissing(FileNotFoundError):
            """The remote asset is absent, a normal optional result."""
            pass

        class Response(Protocol):
            """The transport contract supplies byte chunks and an owned handle."""

            def read(self, size: int) -> bytes:
                """Return the next chunk; empty bytes denotes end of stream."""
                ...

            def close(self) -> None:
                """Release the response once; transport errors may propagate."""
                ...

        Opener = Callable[[], Response]
    '''},
    '''
        import os
        from pathlib import Path
        from errors import AssetMissing, Opener

        def download(opener: Opener, target, replace=os.replace):
            """Download into a caller-selected destination.

            The caller owns the destination path; we own an opened response.
            """
            try:
                response = opener()
            except AssetMissing:
                return False
            target = Path(target)
            with target.open("wb") as output:
                while True:
                    chunk = response.read(65536)
                    if not chunk:
                        break
                    output.write(chunk)
            response.close()
            return True
    ''',
    '''
        import os
        from pathlib import Path
        import tempfile
        from errors import AssetMissing, Opener

        def download(opener: Opener, target, replace=os.replace):
            try:
                response = opener()
            except AssetMissing:
                return False
            temporary = None
            target = Path(target)
            try:
                try:
                    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as output:
                        temporary = Path(output.name)
                        while True:
                            chunk = response.read(65536)
                            if not chunk:
                                break
                            output.write(chunk)
                except BaseException:
                    try:
                        response.close()
                    except Exception:
                        pass
                    raise
                response.close()
                replace(temporary, target)
                return True
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
    ''',
    '''
        from pathlib import Path
        import tempfile
        import unittest
        from app import download
        from errors import AssetMissing

        class Response:
            def __init__(self, chunks, read_error=None, close_error=None):
                self.chunks = list(chunks)
                self.read_error = read_error
                self.close_error = close_error
                self.closed = 0
            def read(self, size):
                if self.chunks:
                    return self.chunks.pop(0)
                if self.read_error:
                    raise self.read_error
                return b""
            def close(self):
                self.closed += 1
                if self.close_error:
                    raise self.close_error

        class Contract(unittest.TestCase):
            def setUp(self):
                temporary = tempfile.TemporaryDirectory()
                self.addCleanup(temporary.cleanup)
                self.directory = Path(temporary.name)
                self.target = self.directory / "asset.bin"
                self.target.write_bytes(b"old")

            def assert_only_target(self):
                self.assertEqual(sorted(p.name for p in self.directory.iterdir()), ["asset.bin"])

            def test_success(self):
                response = Response([b"ab", b"cd"])
                self.assertTrue(download(lambda: response, self.target))
                self.assertEqual(self.target.read_bytes(), b"abcd")
                self.assertEqual(response.closed, 1)
                self.assert_only_target()

            def test_read_failure_preserves_target(self):
                error, response = OSError("read"), Response([b"partial"])
                response.read_error = error
                with self.assertRaises(OSError) as found:
                    download(lambda: response, self.target)
                self.assertIs(found.exception, error)
                self.assertEqual(self.target.read_bytes(), b"old")
                self.assertEqual(response.closed, 1)
                self.assert_only_target()

            def test_create_failure_closes(self):
                response = Response([b"x"])
                with self.assertRaises(FileNotFoundError):
                    download(lambda: response, self.directory / "absent" / "x")
                self.assertEqual(response.closed, 1)
                self.assertEqual(self.target.read_bytes(), b"old")

            def test_missing_is_normal(self):
                def opener():
                    raise AssetMissing("missing")
                self.assertFalse(download(opener, self.target))
                self.assertEqual(self.target.read_bytes(), b"old")

            def test_open_failure_propagates(self):
                error = TimeoutError("open")
                def opener():
                    raise error
                with self.assertRaises(TimeoutError) as found:
                    download(opener, self.target)
                self.assertIs(found.exception, error)
                self.assert_only_target()

            def test_close_failure_no_replace(self):
                error, response = OSError("close"), Response([b"new"])
                response.close_error = error
                with self.assertRaises(OSError) as found:
                    download(lambda: response, self.target)
                self.assertIs(found.exception, error)
                self.assertEqual(response.closed, 1)
                self.assertEqual(self.target.read_bytes(), b"old")
                self.assert_only_target()

            def test_close_does_not_mask_read(self):
                error = RuntimeError("primary")
                response = Response([], read_error=error, close_error=OSError("secondary"))
                with self.assertRaises(RuntimeError) as found:
                    download(lambda: response, self.target)
                self.assertIs(found.exception, error)
                self.assertEqual(response.closed, 1)

            def test_replace_failure_cleanup(self):
                response = Response([b"new"])
                def fail_replace(source, target):
                    raise PermissionError("replace")
                with self.assertRaises(PermissionError):
                    download(lambda: response, self.target, replace=fail_replace)
                self.assertEqual(self.target.read_bytes(), b"old")
                self.assertEqual(response.closed, 1)
                self.assert_only_target()

            def test_empty_resource(self):
                response = Response([])
                self.assertTrue(download(lambda: response, self.target))
                self.assertEqual(self.target.read_bytes(), b"")
                self.assertEqual(response.closed, 1)

            def test_base_exception_cleanup(self):
                response = Response([], read_error=KeyboardInterrupt())
                with self.assertRaises(KeyboardInterrupt):
                    download(lambda: response, self.target)
                self.assertEqual(response.closed, 1)
                self.assertEqual(self.target.read_bytes(), b"old")
                self.assert_only_target()
    ''',
    ["拥有的response恰好关闭一次", "失败不覆盖旧文件", "临时文件清理", "保留主异常和正常缺失"])

add(9, "async-cancellation-and-release",
    "异步 run_job(pool, handler, job, metrics) 必须取得pool租约，在handler完成、拒绝、异常或取消后均释放租约。job为非空字符串。handler正常完成时返回其结果并将metrics['success']加1；该计数表示handler正常完成次数。JobRejected是正常业务拒绝，返回{'status':'rejected'}且不增加成功数；其他异常（包括asyncio.CancelledError）原样传播。取得租约失败时不能调用handler。使用现有Lease的生命周期接口，修复吞取消和租约泄漏，保留同job排他性。",
    {"leases.py": '''
        import asyncio

        class JobRejected(ValueError):
            pass
        class LeaseBusy(RuntimeError):
            pass

        class Lease:
            def __init__(self, pool, job):
                self.pool, self.job, self.closed = pool, job, False

            async def __aenter__(self):
                return self

            async def __aexit__(self, kind, value, traceback):
                await self.close()
                return False

            async def close(self):
                if not self.closed:
                    self.closed = True
                    self.pool.active.remove(self.job)
                    self.pool.released.append(self.job)
                    await asyncio.sleep(0)

        class Pool:
            def __init__(self):
                self.active = set()
                self.acquired, self.released = [], []
                self.error = None

            async def acquire(self, job):
                if self.error:
                    raise self.error
                if job in self.active:
                    raise LeaseBusy(job)
                self.active.add(job)
                self.acquired.append(job)
                return Lease(self, job)
    '''},
    '''
        from leases import JobRejected, LeaseBusy

        async def run_job(pool, handler, job, metrics):
            if not isinstance(job, str) or not job.strip():
                raise ValueError("job")
            lease = await pool.acquire(job)
            try:
                result = await handler(job)
            except BaseException:
                return {"status": "rejected"}
            metrics["success"] = metrics.get("success", 0) + 1
            await lease.close()
            return result
    ''',
    '''
        from leases import JobRejected, LeaseBusy

        async def run_job(pool, handler, job, metrics):
            if not isinstance(job, str) or not job.strip():
                raise ValueError("job")
            lease = await pool.acquire(job)
            async with lease:
                try:
                    result = await handler(job)
                except JobRejected:
                    return {"status": "rejected"}
                metrics["success"] = metrics.get("success", 0) + 1
                return result
    ''',
    '''
        import asyncio
        import unittest
        from app import run_job
        from leases import Pool, JobRejected, LeaseBusy

        class Contract(unittest.IsolatedAsyncioTestCase):
            async def asyncSetUp(self):
                self.pool, self.metrics = Pool(), {}

            async def test_success(self):
                async def handler(job):
                    return "result:" + job
                self.assertEqual(await run_job(self.pool, handler, "A", self.metrics), "result:A")
                self.assertEqual(self.metrics, {"success": 1})
                self.assertEqual(self.pool.released, ["A"])
                self.assertEqual(self.pool.active, set())

            async def test_expected_rejection(self):
                async def handler(job):
                    raise JobRejected("policy")
                self.assertEqual(await run_job(self.pool, handler, "A", self.metrics), {"status": "rejected"})
                self.assertEqual(self.pool.released, ["A"])
                self.assertEqual(self.metrics.get("success", 0), 0)

            async def test_unexpected_propagates(self):
                error = RuntimeError("bug")
                async def handler(job):
                    raise error
                with self.assertRaises(RuntimeError) as found:
                    await run_job(self.pool, handler, "A", self.metrics)
                self.assertIs(found.exception, error)
                self.assertEqual(self.pool.released, ["A"])

            async def test_cancelled_error_propagates(self):
                async def handler(job):
                    raise asyncio.CancelledError()
                with self.assertRaises(asyncio.CancelledError):
                    await run_job(self.pool, handler, "A", self.metrics)
                self.assertEqual(self.pool.released, ["A"])
                self.assertEqual(self.metrics.get("success", 0), 0)

            async def test_real_task_cancellation(self):
                entered = asyncio.Event()
                async def handler(job):
                    entered.set()
                    await asyncio.Event().wait()
                task = asyncio.create_task(run_job(self.pool, handler, "A", self.metrics))
                await asyncio.wait_for(entered.wait(), 5)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertEqual(self.pool.active, set())
                self.assertEqual(self.pool.released, ["A"])

            async def test_acquire_failure_no_handler(self):
                calls = []
                self.pool.error = OSError("pool")
                async def handler(job):
                    calls.append(job)
                with self.assertRaises(OSError):
                    await run_job(self.pool, handler, "A", self.metrics)
                self.assertEqual(calls, [])
                self.assertEqual(self.pool.released, [])

            async def test_existing_lease_preserved(self):
                outer = await self.pool.acquire("A")
                async def handler(job):
                    self.fail("handler cannot run")
                with self.assertRaises(LeaseBusy):
                    await run_job(self.pool, handler, "A", self.metrics)
                self.assertEqual(self.pool.active, {"A"})
                await outer.close()

            async def test_reacquire_after_failure(self):
                async def fail(job):
                    raise JobRejected()
                async def succeed(job):
                    return 3
                await run_job(self.pool, fail, "A", self.metrics)
                self.assertEqual(await run_job(self.pool, succeed, "A", self.metrics), 3)
                self.assertEqual(self.pool.released, ["A", "A"])

            async def test_metrics_not_reset(self):
                self.metrics["success"] = 4
                async def handler(job):
                    return 1
                await run_job(self.pool, handler, "A", self.metrics)
                self.assertEqual(self.metrics["success"], 5)

            async def test_invalid_job(self):
                async def handler(job):
                    self.fail("invalid input")
                for job in (None, "", " ", 3):
                    with self.subTest(job=job), self.assertRaises(ValueError):
                        await run_job(self.pool, handler, job, self.metrics)
                self.assertEqual(self.pool.acquired, [])
    ''',
    ["取消原样传播", "拒绝不是系统成功", "租约所有退出路径释放", "成功计数和同job排他"])

add(10, "mutable-plugin-boundary",
    "process(store, payload, hook=None)接收sku、quantity、labels；sku为非空字符串，quantity严格正整数，labels是字符串列表。非法数据抛OrderError，不能调用hook或store。自定义hook接收只含这三个业务字段的可修改独立副本，返回None，可能修改quantity或labels；它返回后需确保数据仍合法，再保存不可变Record。hook异常原样传播且不保存；不能修改调用者输入。hook保留引用并稍后再修改也不能改变已保存Record。无hook路径保持正常功能，不因类型注解把hook后的值当成已验证。",
    {"domain.py": '''
        from dataclasses import dataclass

        class OrderError(ValueError):
            pass

        @dataclass(frozen=True)
        class Record:
            sku: str
            quantity: int
            labels: tuple

        def validate(payload):
            if not isinstance(payload, dict):
                raise OrderError("payload")
            sku, quantity, labels = payload.get("sku"), payload.get("quantity"), payload.get("labels")
            if not isinstance(sku, str) or not sku.strip():
                raise OrderError("sku")
            if type(quantity) is not int or quantity <= 0:
                raise OrderError("quantity")
            if not isinstance(labels, list) or any(not isinstance(item, str) for item in labels):
                raise OrderError("labels")

        class Store:
            def __init__(self):
                self.rows = []

            def save(self, record):
                self.rows.append(record)
                return record
    '''},
    '''
        from domain import OrderError, Record, Store, validate

        def process(store, payload, hook=None):
            """Process an external order through an optional mutable plugin.

            Saving is the side effect; earlier failures must not persist a row.
            """
            validate(payload)
            owned = dict(payload)
            if hook is not None:
                hook(owned)
            record = Record(owned["sku"], owned["quantity"], owned["labels"])
            return store.save(record)
    ''',
    '''
        from domain import OrderError, Record, Store, validate

        def process(store, payload, hook=None):
            validate(payload)
            owned = {"sku": payload["sku"], "quantity": payload["quantity"], "labels": list(payload["labels"])}
            if hook is not None:
                hook(owned)
                validate(owned)
            record = Record(owned["sku"], owned["quantity"], tuple(owned["labels"]))
            return store.save(record)
    ''',
    '''
        import dataclasses
        import unittest
        from app import process
        from domain import Store, Record, OrderError

        class Contract(unittest.TestCase):
            def setUp(self):
                self.store = Store()
            def payload(self):
                return {"sku": "A", "quantity": 2, "labels": ["new"]}

            def test_normal(self):
                self.assertEqual(process(self.store, self.payload()), Record("A", 2, ("new",)))

            def test_hook_valid_changes(self):
                calls = []
                def hook(value):
                    calls.append(1)
                    value["quantity"] = 4
                    value["labels"].append("plugin")
                result = process(self.store, self.payload(), hook)
                self.assertEqual(result, Record("A", 4, ("new", "plugin")))
                self.assertEqual(calls, [1])

            def test_invalid_initial_skips_hook(self):
                payload, calls = self.payload(), []
                payload["quantity"] = 0
                with self.assertRaises(OrderError):
                    process(self.store, payload, lambda value: calls.append(1))
                self.assertEqual(calls, [])
                self.assertEqual(self.store.rows, [])

            def test_invalid_after_hook(self):
                def hook(value):
                    value["quantity"] = 0
                with self.assertRaises(OrderError):
                    process(self.store, self.payload(), hook)
                self.assertEqual(self.store.rows, [])

            def test_bool_after_hook(self):
                def hook(value):
                    value["quantity"] = True
                with self.assertRaises(OrderError):
                    process(self.store, self.payload(), hook)
                self.assertEqual(self.store.rows, [])

            def test_bad_labels_after_hook(self):
                def hook(value):
                    value["labels"] = "oops"
                with self.assertRaises(OrderError):
                    process(self.store, self.payload(), hook)

            def test_hook_error_propagates(self):
                error = RuntimeError("plugin")
                def hook(value):
                    raise error
                with self.assertRaises(RuntimeError) as found:
                    process(self.store, self.payload(), hook)
                self.assertIs(found.exception, error)
                self.assertEqual(self.store.rows, [])

            def test_input_ownership(self):
                payload = self.payload()
                def hook(value):
                    value["labels"].append("changed")
                process(self.store, payload, hook)
                self.assertEqual(payload, self.payload())

            def test_stashed_hook_reference(self):
                captured = []
                result = process(self.store, self.payload(), lambda value: captured.append(value))
                captured[0]["labels"].append("later")
                captured[0]["quantity"] = 999
                self.assertEqual(result, Record("A", 2, ("new",)))

            def test_result_frozen(self):
                result = process(self.store, self.payload())
                with self.assertRaises(dataclasses.FrozenInstanceError):
                    result.quantity = 0
    ''',
    ["插件前后边界验证", "回调异常传播", "嵌套副本与不可变保存", "失败前无持久副作用"])

add(11, "tenant-authorization-before-cache",
    "租户文档 TenantService.get(principal, tenant, doc_id) 支持缓存，但每次请求都要先调用现有authorizer.ensure校验当前权限，再读取缓存或仓库；已命中缓存也不能绕过撤权。缓存键应含tenant和doc_id，避免不同租户同ID串值。文档不存在返回None并可缓存，仓库其他异常必须原样传播，失败不缓存。principal需非空字符串；tenant和doc_id也必须非空字符串。保留正常缓存命中不重复查库，修复权限和租户隔离。",
    {"auth.py": '''
        class PermissionDenied(PermissionError):
            pass

        class Authorizer:
            def __init__(self):
                self.grants = set()
                self.calls = []

            def ensure(self, principal, tenant):
                self.calls.append((principal, tenant))
                if not isinstance(principal, str) or not principal.strip():
                    raise PermissionDenied("authentication required")
                if (principal, tenant) not in self.grants:
                    raise PermissionDenied("tenant denied")

        class Repository:
            def __init__(self):
                self.rows = {}
                self.calls = []
                self.error = None

            def get(self, tenant, doc_id):
                self.calls.append((tenant, doc_id))
                if self.error:
                    raise self.error
                return self.rows.get((tenant, doc_id))
    '''},
    '''
        from auth import PermissionDenied

        class TenantService:
            def __init__(self, repository, authorizer):
                self.repository = repository
                self.authorizer = authorizer
                self.cache = {}

            def get(self, principal, tenant, doc_id):
                if not isinstance(tenant, str) or not tenant.strip():
                    raise ValueError("tenant")
                if not isinstance(doc_id, str) or not doc_id.strip():
                    raise ValueError("doc_id")
                if doc_id in self.cache:
                    return self.cache[doc_id]
                self.authorizer.ensure(principal, tenant)
                value = self.repository.get(tenant, doc_id)
                self.cache[doc_id] = value
                return value
    ''',
    '''
        from auth import PermissionDenied

        class TenantService:
            def __init__(self, repository, authorizer):
                self.repository = repository
                self.authorizer = authorizer
                self.cache = {}

            def get(self, principal, tenant, doc_id):
                if not isinstance(tenant, str) or not tenant.strip():
                    raise ValueError("tenant")
                if not isinstance(doc_id, str) or not doc_id.strip():
                    raise ValueError("doc_id")
                self.authorizer.ensure(principal, tenant)
                key = (tenant, doc_id)
                if key not in self.cache:
                    self.cache[key] = self.repository.get(tenant, doc_id)
                return self.cache[key]
    ''',
    '''
        import unittest
        from app import TenantService
        from auth import Authorizer, Repository, PermissionDenied

        class Contract(unittest.TestCase):
            def setUp(self):
                self.auth, self.repo = Authorizer(), Repository()
                self.auth.grants.update({("u", "A"), ("u", "B")})
                self.repo.rows.update({("A", "doc"): "A-value", ("B", "doc"): "B-value"})
                self.service = TenantService(self.repo, self.auth)

            def test_authorized(self):
                self.assertEqual(self.service.get("u", "A", "doc"), "A-value")

            def test_fresh_cache_no_repository(self):
                self.service.get("u", "A", "doc")
                self.service.get("u", "A", "doc")
                self.assertEqual(self.repo.calls, [("A", "doc")])

            def test_tenant_collision(self):
                self.assertEqual(self.service.get("u", "A", "doc"), "A-value")
                self.assertEqual(self.service.get("u", "B", "doc"), "B-value")

            def test_revoke_warm_cache(self):
                self.service.get("u", "A", "doc")
                self.auth.grants.remove(("u", "A"))
                with self.assertRaises(PermissionDenied):
                    self.service.get("u", "A", "doc")

            def test_anonymous_warm_cache(self):
                self.service.get("u", "A", "doc")
                with self.assertRaises(PermissionDenied):
                    self.service.get(None, "A", "doc")

            def test_denied_before_repository(self):
                with self.assertRaises(PermissionDenied):
                    self.service.get("other", "A", "doc")
                self.assertEqual(self.repo.calls, [])

            def test_none_cached(self):
                self.assertIsNone(self.service.get("u", "A", "missing"))
                self.assertIsNone(self.service.get("u", "A", "missing"))
                self.assertEqual(self.repo.calls, [("A", "missing")])

            def test_authorizer_every_request(self):
                for _ in range(3):
                    self.service.get("u", "A", "doc")
                self.assertEqual(self.auth.calls, [("u", "A")] * 3)

            def test_repository_failure_not_cached(self):
                error = OSError("database")
                self.repo.error = error
                with self.assertRaises(OSError) as found:
                    self.service.get("u", "A", "doc")
                self.assertIs(found.exception, error)
                self.assertEqual(self.service.cache, {})

            def test_invalid_keys(self):
                for tenant, doc in (("", "x"), (None, "x"), ("A", ""), ("A", 1)):
                    with self.subTest(tenant=tenant, doc=doc), self.assertRaises(ValueError):
                        self.service.get("u", tenant, doc)
                self.assertEqual(self.repo.calls, [])
    ''',
    ["缓存前每次鉴权", "撤权立即生效", "租户键隔离", "正常不存在和存储异常区分"])

add(12, "lazy-consumption-and-failure-timing",
    "chunks(records, size, limit=None) 应惰性产生tuple批次，size为严格正整数，limit为None或非负严格整数（bool拒绝）；非法类型抛TypeError、非法数值抛ValueError，允许在首次迭代时验证参数。limit=0不得消费输入，limit限制批次数，不得预读下一批；尾部不足size仍产出，不产空批。输入迭代异常应在已产出的完整批次之后原样传播。export(records,sink,size,limit)顺序写批并返回已写记录数；sink异常传播并立即停止消费。修复当前提前物化流程。",
    {"exporter.py": '''
        def export(records, sink, size, limit=None):
            # Import here so this entry point uses the current chunks implementation.
            from app import chunks
            written = 0
            for batch in chunks(records, size, limit):
                sink(batch)
                written += len(batch)
            return written

        class MemorySink:
            def __init__(self):
                self.batches = []

            def __call__(self, batch):
                self.batches.append(batch)
    '''},
    '''
        from exporter import export

        def chunks(records, size, limit=None):
            """Batch an export source, optionally limiting the number of batches.

            Each batch is immutable and records keep their original order.
            Consumers may stop after any yielded batch.
            A source can be a one-shot iterator with observable side effects.
            Errors belong to the stage which encounters them.
            """
            if type(size) is not int:
                raise TypeError("size")
            if size <= 0:
                raise ValueError("size")
            if limit is not None and type(limit) is not int:
                raise TypeError("limit")
            if limit is not None and limit < 0:
                raise ValueError("limit")
            data = list(records)
            count = 0
            for offset in range(0, len(data), size):
                if limit is not None and count >= limit:
                    return
                yield tuple(data[offset:offset + size])
                count += 1
    ''',
    '''
        from itertools import islice
        from exporter import export

        def chunks(records, size, limit=None):
            """Consume only the current batch and preserve source exception timing."""
            if type(size) is not int:
                raise TypeError("size")
            if size <= 0:
                raise ValueError("size")
            if limit is not None and type(limit) is not int:
                raise TypeError("limit")
            if limit is not None and limit < 0:
                raise ValueError("limit")
            if limit == 0:
                return
            iterator = iter(records)
            count = 0
            while limit is None or count < limit:
                batch = tuple(islice(iterator, size))
                if not batch:
                    return
                yield batch
                count += 1
    ''',
    '''
        import unittest
        from app import chunks
        from exporter import export, MemorySink

        class TailFailure(RuntimeError):
            pass

        class Contract(unittest.TestCase):
            def test_normal_batches(self):
                self.assertEqual(list(chunks([1, 2, 3], 2)), [(1, 2), (3,)])

            def test_empty(self):
                self.assertEqual(list(chunks([], 2)), [])

            def test_zero_limit_no_consumption(self):
                consumed = []
                def source():
                    consumed.append(1)
                    yield 1
                self.assertEqual(list(chunks(source(), 2, 0)), [])
                self.assertEqual(consumed, [])

            def test_prefix_before_source_error(self):
                error = TailFailure("tail")
                def source():
                    yield 1
                    yield 2
                    raise error
                iterator = chunks(source(), 2)
                self.assertEqual(next(iterator), (1, 2))
                with self.assertRaises(TailFailure) as found:
                    next(iterator)
                self.assertIs(found.exception, error)

            def test_limit_does_not_read_tail(self):
                def source():
                    yield 1
                    yield 2
                    raise TailFailure("must not read")
                self.assertEqual(list(chunks(source(), 2, 1)), [(1, 2)])

            def test_bad_size_type(self):
                for size in (True, "2", 2.0):
                    with self.subTest(size=size), self.assertRaises(TypeError):
                        list(chunks([], size))

            def test_bad_size_value(self):
                for size in (0, -1):
                    with self.subTest(size=size), self.assertRaises(ValueError):
                        list(chunks([], size))

            def test_bad_limit(self):
                with self.assertRaises(TypeError):
                    list(chunks([], 1, True))
                with self.assertRaises(ValueError):
                    list(chunks([], 1, -1))

            def test_sink_failure_stops_consumption(self):
                consumed, calls = [], []
                def source():
                    for value in range(5):
                        consumed.append(value)
                        yield value
                error = OSError("sink")
                def sink(batch):
                    calls.append(batch)
                    raise error
                with self.assertRaises(OSError) as found:
                    export(source(), sink, 2)
                self.assertIs(found.exception, error)
                self.assertEqual(consumed, [0, 1])
                self.assertEqual(calls, [(0, 1)])

            def test_export_count(self):
                sink = MemorySink()
                self.assertEqual(export([1, 2, 3], sink, 2), 3)
                self.assertEqual(sink.batches, [(1, 2), (3,)])
    ''',
    ["惰性读取与批次数限制", "零限制不消费", "异常时机和原异常", "sink失败不预读"])


def write_files(directory, files):
    directory.mkdir(parents=True, exist_ok=True)
    for relative, content in files.items():
        destination = directory / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")


def main():
    assert len(CASES) == 12
    work = HERE / ".preflight-work"
    work.mkdir(exist_ok=True)
    temp = work / "tmp"
    temp.mkdir(exist_ok=True)
    env = os.environ.copy()
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8", TMP=str(temp), TEMP=str(temp), TMPDIR=str(temp))
    results = []
    for case in CASES:
        output = HERE / (case["id"] + ".json")
        output.write_text(json.dumps(case, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        item = {"id": case["id"], "phases": {}}
        for phase, files in (("initial", case["files"]), ("reference", case["reference_files"])):
            directory = work / case["id"] / phase
            write_files(directory, files)
            write_files(directory, case["hidden_tests"])
            start = time.monotonic()
            result = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-s", ".", "-p", "test_contract.py", "-v"],
                                    cwd=directory, env=env, capture_output=True, text=True, encoding="utf-8", timeout=30)
            text = result.stdout + result.stderr
            (directory / "unittest.log").write_text(text, encoding="utf-8")
            match = re.search(r"Ran (\d+) tests? in", text)
            count = int(match.group(1)) if match else 0
            failure_match = re.search(r"failures=(\d+)", text)
            error_match = re.search(r"errors=(\d+)", text)
            item["phases"][phase] = {"exit_code": result.returncode, "tests": count,
                                     "failures": int(failure_match.group(1)) if failure_match else 0,
                                     "errors": int(error_match.group(1)) if error_match else 0,
                                     "elapsed_seconds": round(time.monotonic() - start, 4),
                                     "log_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}
        item["eligible"] = (item["phases"]["initial"]["exit_code"] != 0
                            and item["phases"]["reference"]["exit_code"] == 0
                            and item["phases"]["initial"]["tests"] == item["phases"]["reference"]["tests"]
                            and item["phases"]["reference"]["tests"] >= 8)
        results.append(item)
    preflight = {"interpreter": sys.version, "case_count": len(CASES), "results": results,
                 "reference_tests": sum(row["phases"]["reference"]["tests"] for row in results),
                 "all_eligible": all(row["eligible"] for row in results),
                 "model_calls": 0, "network_calls": 0}
    (HERE / "preflight.json").write_text(json.dumps(preflight, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest = {"schema_version": 1, "purpose": "Sealed independent production-maintenance holdout",
                "case_count": len(CASES), "reference_tests": preflight["reference_tests"],
                "cases": [{"id": case["id"], "family": case["family"],
                           "checks": next(row for row in results if row["id"] == case["id"])["phases"]["reference"]["tests"],
                           "file": case["id"] + ".json",
                           "sha256": hashlib.sha256((HERE / (case["id"] + ".json")).read_bytes()).hexdigest().upper()}
                          for case in CASES],
                "preflight_sha256": hashlib.sha256((HERE / "preflight.json").read_bytes()).hexdigest().upper(),
                "all_eligible": preflight["all_eligible"],
                "source": "New hand-authored task contracts, not selected from model outcomes; synthetic production patterns, not field-production evidence."}
    (HERE / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"case_count": len(CASES), "reference_tests": preflight["reference_tests"],
                      "all_eligible": preflight["all_eligible"],
                      "failed_reference_cases": [row["id"] for row in results if row["phases"]["reference"]["exit_code"] != 0]}))
    return 0 if preflight["all_eligible"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
