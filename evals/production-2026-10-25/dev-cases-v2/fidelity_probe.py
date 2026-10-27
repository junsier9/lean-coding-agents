"""Compare real SQLite context behavior with the exact repaired Tracked class."""
from __future__ import annotations

import argparse
import ast
import io
import json
from pathlib import Path
import sqlite3
import sys
import unittest

HERE = Path(__file__).resolve().parent
CASE = json.loads((HERE / "d06.json").read_text(encoding="utf-8"))
TREE = ast.parse(CASE["hidden_tests"]["test_contract.py"])
NODE = next(node for node in TREE.body if isinstance(node, ast.ClassDef) and node.name == "Tracked")
NAMESPACE = {"sqlite3": sqlite3}
exec(compile(ast.Module(body=[NODE], type_ignores=[]), "<repaired-d06-Tracked>", "exec"), NAMESPACE)
Tracked = NAMESPACE["Tracked"]
OBSERVATIONS = {}


def observe(scenario, proxy=False):
    raw = sqlite3.connect(":memory:", isolation_level=None)
    raw.execute("PRAGMA foreign_keys = ON")
    raw.execute("CREATE TABLE items (value INTEGER)")
    if scenario == "deferred_commit_failure":
        raw.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
        raw.execute("CREATE TABLE child (parent_id INTEGER REFERENCES parent(id) DEFERRABLE INITIALLY DEFERRED)")
    transactions = []
    raw.set_trace_callback(lambda statement: transactions.append(statement) if statement.split()[0].upper() in ("BEGIN", "COMMIT", "ROLLBACK") else None)
    if scenario == "authorizer_commit_failure":
        def authorize(action, first, second, database, trigger):
            if action == sqlite3.SQLITE_TRANSACTION and first == "COMMIT":
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        raw.set_authorizer(authorize)
    connection = Tracked(raw) if proxy else raw
    body_error = KeyboardInterrupt("body interrupted") if scenario == "keyboard_interrupt" else RuntimeError("body failed")
    caught, entered_self = None, None
    try:
        if scenario == "already_closed_enter":
            connection.close()
        with connection as entered:
            entered_self = entered is connection
            if scenario == "close_inside":
                connection.close()
            elif scenario == "no_active_transaction":
                connection.execute("SELECT 1")
            elif scenario == "autocommit_write_then_error":
                connection.execute("INSERT INTO items VALUES (1)")
                raise body_error
            else:
                connection.execute("BEGIN IMMEDIATE")
                if scenario == "deferred_commit_failure":
                    connection.execute("INSERT INTO child VALUES (99)")
                else:
                    connection.execute("INSERT INTO items VALUES (1)")
                if scenario in ("runtime_error", "keyboard_interrupt"):
                    raise body_error
    except BaseException as error:
        caught = error

    try:
        raw.execute("SELECT 1")
        open_after_context = True
    except sqlite3.ProgrammingError:
        open_after_context = False
    result = {
        "entered_returns_self": entered_self,
        "exception_type": type(caught).__name__ if caught is not None else None,
        "exception_message": str(caught) if caught is not None else None,
        "original_body_exception_propagated": caught is body_error if scenario in (
            "runtime_error", "keyboard_interrupt", "autocommit_write_then_error") else None,
        "open_after_context": open_after_context,
        "transaction_sql": transactions,
    }
    if open_after_context:
        table = "child" if scenario == "deferred_commit_failure" else "items"
        result["rows_after_context"] = [list(row) for row in raw.execute("SELECT * FROM " + table)]
        result["active_transaction_after_context"] = raw.in_transaction
        if proxy:
            result["proxy_close_calls_before_explicit_close"] = connection.closed
            result["proxy_rollback_calls"] = connection.rollbacks
        connection.close()
    else:
        result["rows_after_context"] = None
        result["active_transaction_after_context"] = None
        if proxy:
            result["proxy_close_calls_before_explicit_close"] = connection.closed
            result["proxy_rollback_calls"] = connection.rollbacks
    try:
        raw.execute("SELECT 1")
        result["explicit_close_is_effective"] = False
    except sqlite3.ProgrammingError:
        result["explicit_close_is_effective"] = True
    if proxy:
        result["proxy_total_close_calls"] = connection.closed
    return result


def observe_injected_commit(mode):
    raw = sqlite3.connect(":memory:", isolation_level=None)
    raw.execute("CREATE TABLE items (value INTEGER)")
    connection = Tracked(raw, fail_commit=True)
    original_commit = connection.commit
    seen_errors = []
    commit_calls = []
    def monitored_commit():
        commit_calls.append(True)
        try:
            return original_commit()
        except BaseException as error:
            seen_errors.append(error)
            raise
    connection.commit = monitored_commit
    caught = None
    try:
        if mode == "explicit":
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("INSERT INTO items VALUES (1)")
            try:
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        else:
            with connection:
                if mode == "context":
                    connection.execute("BEGIN IMMEDIATE")
                    connection.execute("INSERT INTO items VALUES (1)")
                else:
                    connection.execute("SELECT 1")
    except BaseException as error:
        caught = error
    result = {
        "exception_type": type(caught).__name__ if caught is not None else None,
        "exception_message": str(caught) if caught is not None else None,
        "commit_calls_through_existing_fault_hook": len(commit_calls),
        "caught_commit_exception_is_original": bool(seen_errors) and caught is seen_errors[0],
        "rows_after_context": [list(row) for row in raw.execute("SELECT value FROM items")],
        "active_transaction_after_context": raw.in_transaction,
        "close_calls_before_explicit_close": connection.closed,
        "rollback_calls": connection.rollbacks,
    }
    connection.close()
    result["total_close_calls"] = connection.closed
    return result


class Fidelity(unittest.TestCase):
    def compare(self, scenario):
        real, proxy = observe(scenario), observe(scenario, proxy=True)
        OBSERVATIONS[scenario] = {"real": real, "double": proxy}
        comparable = {key: value for key, value in proxy.items() if not key.startswith("proxy_")}
        self.assertEqual(real, comparable)
        self.assertTrue(real["explicit_close_is_effective"])
        self.assertEqual(proxy["proxy_total_close_calls"], 1)
        if real["open_after_context"]:
            self.assertEqual(proxy["proxy_close_calls_before_explicit_close"], 0)
            self.assertFalse(real["active_transaction_after_context"])
        return real, proxy

    def test_active_success_commits_without_close(self):
        real, proxy = self.compare("active_success")
        self.assertTrue(real["entered_returns_self"])
        self.assertEqual(real["rows_after_context"], [[1]])
        self.assertEqual(real["transaction_sql"], ["BEGIN IMMEDIATE", "COMMIT"])
        self.assertEqual(proxy["proxy_rollback_calls"], 0)

    def test_no_transaction_does_not_commit_or_rollback(self):
        real, proxy = self.compare("no_active_transaction")
        self.assertTrue(real["entered_returns_self"])
        self.assertEqual(real["transaction_sql"], [])
        self.assertEqual(proxy["proxy_rollback_calls"], 0)

    def test_autocommit_context_does_not_invent_atomic_transaction(self):
        real, proxy = self.compare("autocommit_write_then_error")
        self.assertEqual(real["rows_after_context"], [[1]])
        self.assertEqual(real["exception_type"], "RuntimeError")
        self.assertTrue(real["original_body_exception_propagated"])
        self.assertEqual(real["transaction_sql"], [])
        self.assertEqual(proxy["proxy_rollback_calls"], 0)

    def test_runtime_error_rolls_back_original_error(self):
        real, proxy = self.compare("runtime_error")
        self.assertEqual(real["rows_after_context"], [])
        self.assertTrue(real["original_body_exception_propagated"])
        self.assertEqual(real["exception_type"], "RuntimeError")
        self.assertEqual(proxy["proxy_rollback_calls"], 1)

    def test_keyboard_interrupt_rolls_back_original_error(self):
        real, proxy = self.compare("keyboard_interrupt")
        self.assertEqual(real["rows_after_context"], [])
        self.assertTrue(real["original_body_exception_propagated"])
        self.assertEqual(real["exception_type"], "KeyboardInterrupt")
        self.assertEqual(proxy["proxy_rollback_calls"], 1)

    def test_native_deferred_commit_error_rolls_back(self):
        real, proxy = self.compare("deferred_commit_failure")
        self.assertEqual(real["exception_type"], "IntegrityError")
        self.assertEqual(real["rows_after_context"], [])
        self.assertEqual(real["transaction_sql"], ["BEGIN IMMEDIATE", "COMMIT", "ROLLBACK"])
        self.assertEqual(proxy["proxy_rollback_calls"], 1)

    def test_native_authorizer_commit_error_rolls_back(self):
        real, proxy = self.compare("authorizer_commit_failure")
        self.assertEqual(real["exception_type"], "DatabaseError")
        self.assertEqual(real["rows_after_context"], [])
        self.assertEqual(proxy["proxy_rollback_calls"], 1)

    def test_closed_connection_enter_rejects(self):
        real, proxy = self.compare("already_closed_enter")
        self.assertFalse(real["open_after_context"])
        self.assertEqual(real["exception_type"], "ProgrammingError")
        self.assertIsNone(real["entered_returns_self"])

    def test_close_inside_context_matches_sqlite_exit_failure(self):
        real, proxy = self.compare("close_inside")
        self.assertFalse(real["open_after_context"])
        self.assertEqual(real["exception_type"], "ProgrammingError")

    def check_injected(self, mode):
        result = observe_injected_commit(mode)
        OBSERVATIONS["fail_commit_" + mode] = result
        self.assertEqual(result["exception_type"], "OperationalError")
        self.assertEqual(result["exception_message"], "commit rejected")
        self.assertEqual(result["commit_calls_through_existing_fault_hook"], 1)
        self.assertTrue(result["caught_commit_exception_is_original"])
        self.assertEqual(result["rows_after_context"], [])
        self.assertFalse(result["active_transaction_after_context"])
        self.assertEqual(result["rollback_calls"], 1)
        self.assertEqual(result["close_calls_before_explicit_close"], 0)
        self.assertEqual(result["total_close_calls"], 1)

    def test_explicit_commit_preserves_existing_failure_injection(self):
        self.check_injected("explicit")

    def test_context_commit_preserves_existing_failure_injection(self):
        self.check_injected("context")

    def test_no_active_context_does_not_call_faulty_commit(self):
        result = observe_injected_commit("inactive")
        OBSERVATIONS["fail_commit_inactive"] = result
        self.assertIsNone(result["exception_type"])
        self.assertEqual(result["commit_calls_through_existing_fault_hook"], 0)
        self.assertEqual(result["rollback_calls"], 0)
        self.assertEqual(result["close_calls_before_explicit_close"], 0)
        self.assertEqual(result["total_close_calls"], 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="fidelity.json")
    args = parser.parse_args()
    output = (HERE / args.output).resolve()
    if not output.is_relative_to(HERE):
        raise ValueError("Fidelity output must remain inside the repaired oracle directory")
    log = io.StringIO()
    result = unittest.TextTestRunner(stream=log, verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Fidelity))
    output.with_suffix(".txt").write_text(log.getvalue(), encoding="utf-8")
    report = {
        "tests_ran": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
        "passed": result.wasSuccessful(), "optimize": sys.flags.optimize,
        "sqlite_version": sqlite3.sqlite_version, "python_version": sys.version,
        "compared_actual_repaired_Tracked_class": True,
        "real_connections_use_isolation_level_None": True,
        "model_calls": 0, "observations": OBSERVATIONS,
    }
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("tests_ran", "failures", "errors", "passed", "optimize")}))
    if not result.wasSuccessful():
        print(log.getvalue())
    return int(not result.wasSuccessful())


if __name__ == "__main__":
    raise SystemExit(main())
