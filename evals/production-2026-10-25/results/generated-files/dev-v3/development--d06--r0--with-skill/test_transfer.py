import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from app import transfer
from database import InsufficientFunds, UnknownAccount, connect


class TrackedConnection(sqlite3.Connection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.close_calls = 0
        self.transaction_at_close = None
        self.statements = []
        self.failure_after = None
        self.set_trace_callback(self.statements.append)

    def execute(self, sql, parameters=()):
        result = super().execute(sql, parameters)
        if self.failure_after is not None and sql.startswith(self.failure_after[0]):
            raise self.failure_after[1]
        return result

    def close(self):
        self.close_calls += 1
        self.transaction_at_close = self.in_transaction
        super().close()


class TransferTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "accounts.sqlite"
        self.connections = []
        self.execute_script("""
            CREATE TABLE accounts (id TEXT PRIMARY KEY, cents INTEGER NOT NULL CHECK (cents >= 0));
            INSERT INTO accounts VALUES ('src', 100), ('dst', 50);
        """)

    def execute_script(self, sql):
        connection = connect(self.path)
        try:
            connection.executescript(sql)
        finally:
            connection.close()

    def factory(self, failure_after=None):
        connection = sqlite3.connect(self.path, isolation_level=None, factory=TrackedConnection)
        self.connections.append(connection)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.failure_after = failure_after
        return connection

    def balances(self):
        connection = connect(self.path)
        try:
            return dict(connection.execute("SELECT id, cents FROM accounts"))
        finally:
            connection.close()

    def assert_released(self, outcome):
        connection = self.connections[-1]
        self.assertEqual(connection.close_calls, 1)
        self.assertFalse(connection.transaction_at_close)
        self.assertEqual(connection.statements.count("BEGIN"), 1)
        self.assertEqual(connection.statements[-1], outcome)
        with self.assertRaises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")

    def test_success_commits_both_balances_and_returns_none(self):
        self.assertIsNone(transfer(self.factory, "src", "dst", 25))
        self.assertEqual(self.balances(), {"src": 75, "dst": 75})
        self.assert_released("COMMIT")

    def test_entire_balance_can_be_transferred(self):
        transfer(self.factory, "src", "dst", 100)
        self.assertEqual(self.balances(), {"src": 0, "dst": 150})
        self.assert_released("COMMIT")

    def test_invalid_parameters_never_open_a_connection(self):
        class IntSubclass(int):
            pass

        invalid_accounts = [None, 1, True, b"src", "", [], {}]
        cases = [(value, "dst", 1) for value in invalid_accounts]
        cases += [("src", value, 1) for value in invalid_accounts]
        cases += [("src", "src", 1)]
        cases += [("src", "dst", value) for value in
                  (None, 0, -1, True, False, 1.0, "1", IntSubclass(1))]
        for src, dst, amount in cases:
            with self.subTest(src=src, dst=dst, amount=amount):
                factory = Mock()
                with self.assertRaises(ValueError):
                    transfer(factory, src, dst, amount)
                factory.assert_not_called()

    def test_missing_either_account_rolls_back(self):
        cases = [("missing", "dst", 25, "missing"),
                 ("src", "missing", 25, "missing"),
                 ("src", "missing", 101, "missing"),
                 ("missing", "also missing", 25, "missing")]
        for src, dst, amount, missing in cases:
            with self.subTest(src=src, dst=dst, amount=amount):
                with self.assertRaises(UnknownAccount) as raised:
                    transfer(self.factory, src, dst, amount)
                self.assertEqual(raised.exception.args, (missing,))
                self.assertEqual(self.balances(), {"src": 100, "dst": 50})
                self.assert_released("ROLLBACK")

    def test_insufficient_funds_rolls_back(self):
        with self.assertRaises(InsufficientFunds) as raised:
            transfer(self.factory, "src", "dst", 101)
        self.assertEqual(raised.exception.args, ("src",))
        self.assertEqual(self.balances(), {"src": 100, "dst": 50})
        self.assert_released("ROLLBACK")

    def test_credit_sql_failure_rolls_back_debit(self):
        self.execute_script("""
            CREATE TRIGGER reject_credit BEFORE UPDATE ON accounts
            WHEN NEW.id = 'dst'
            BEGIN SELECT RAISE(ABORT, 'credit refused'); END;
        """)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "credit refused"):
            transfer(self.factory, "src", "dst", 25)
        self.assertEqual(self.balances(), {"src": 100, "dst": 50})
        self.assert_released("ROLLBACK")

    def test_ignored_credit_rolls_back_debit(self):
        self.execute_script("""
            CREATE TRIGGER ignore_credit BEFORE UPDATE ON accounts
            WHEN NEW.id = 'dst'
            BEGIN SELECT RAISE(IGNORE); END;
        """)
        with self.assertRaises(UnknownAccount):
            transfer(self.factory, "src", "dst", 25)
        self.assertEqual(self.balances(), {"src": 100, "dst": 50})
        self.assert_released("ROLLBACK")

    def test_commit_failure_rolls_back_both_updates(self):
        self.execute_script("""
            CREATE TABLE audit (
                account_id TEXT REFERENCES accounts(id) DEFERRABLE INITIALLY DEFERRED
            );
            CREATE TRIGGER invalid_audit AFTER UPDATE ON accounts
            BEGIN INSERT INTO audit VALUES ('missing'); END;
        """)
        with self.assertRaises(sqlite3.IntegrityError):
            transfer(self.factory, "src", "dst", 25)
        self.assertEqual(self.balances(), {"src": 100, "dst": 50})
        self.assert_released("ROLLBACK")

    def test_errors_and_cancellation_propagate_after_debit(self):
        errors = [RuntimeError("unexpected failure"), KeyboardInterrupt(), asyncio.CancelledError()]
        for error in errors:
            with self.subTest(error=type(error).__name__):
                factory = lambda: self.factory(("UPDATE accounts SET cents = cents -", error))
                with self.assertRaises(type(error)) as raised:
                    transfer(factory, "src", "dst", 25)
                self.assertIs(raised.exception, error)
                self.assertEqual(self.balances(), {"src": 100, "dst": 50})
                self.assert_released("ROLLBACK")

    def test_factory_error_propagates(self):
        error = sqlite3.OperationalError("cannot open database")
        factory = Mock(side_effect=error)
        with self.assertRaises(sqlite3.OperationalError) as raised:
            transfer(factory, "src", "dst", 25)
        self.assertIs(raised.exception, error)
        factory.assert_called_once_with()

    def test_account_names_are_sql_data(self):
        src = "src'; DROP TABLE accounts; --"
        dst = 'dst" OR 1=1 --'
        connection = connect(self.path)
        try:
            connection.executemany("INSERT INTO accounts VALUES (?, ?)", [(src, 40), (dst, 5)])
        finally:
            connection.close()
        transfer(self.factory, src, dst, 15)
        self.assertEqual(self.balances(), {"src": 100, "dst": 50, src: 25, dst: 20})
        self.assert_released("COMMIT")


if __name__ == "__main__":
    unittest.main()
