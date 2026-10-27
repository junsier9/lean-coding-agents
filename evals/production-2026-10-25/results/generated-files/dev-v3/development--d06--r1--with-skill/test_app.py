import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app import transfer
from database import InsufficientFunds, UnknownAccount, connect


class RecordingConnection(sqlite3.Connection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.commit_calls = 0
        self.rollback_calls = 0
        self.close_calls = 0
        self.fail_statement = None
        self.execute_error = None
        self.commit_error = None

    def execute(self, sql, parameters=()):
        if self.fail_statement is not None and sql.startswith(self.fail_statement):
            raise self.execute_error
        return super().execute(sql, parameters)

    def commit(self):
        self.commit_calls += 1
        if self.commit_error is not None:
            raise self.commit_error
        return super().commit()

    def rollback(self):
        self.rollback_calls += 1
        return super().rollback()

    def close(self):
        self.close_calls += 1
        return super().close()


class TransferTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "accounts.sqlite"
        self.connections = []
        self.addCleanup(self.close_leaked_connections)
        connection = connect(self.path)
        try:
            connection.executescript(
                "CREATE TABLE accounts (id TEXT PRIMARY KEY, cents INTEGER NOT NULL);"
                "INSERT INTO accounts VALUES ('src', 100), ('dst', 20);"
            )
        finally:
            connection.close()

    def close_leaked_connections(self):
        for connection in self.connections:
            if not connection.close_calls:
                connection.close()

    def factory(self):
        connection = sqlite3.connect(
            self.path, isolation_level=None, factory=RecordingConnection
        )
        self.connections.append(connection)
        return connection

    def balances(self):
        connection = connect(self.path)
        try:
            return dict(connection.execute("SELECT id, cents FROM accounts"))
        finally:
            connection.close()

    def assert_released(self, connection, *, commits=0, rollbacks=1):
        self.assertEqual(connection.commit_calls, commits)
        self.assertEqual(connection.rollback_calls, rollbacks)
        self.assertEqual(connection.close_calls, 1)
        with self.assertRaises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")

    def test_success_commits_and_returns_none(self):
        self.assertIsNone(transfer(self.factory, "src", "dst", 30))
        self.assertEqual(self.balances(), {"src": 70, "dst": 50})
        self.assert_released(self.connections[0], commits=1, rollbacks=0)

    def test_entire_balance_can_be_transferred(self):
        transfer(self.factory, "src", "dst", 100)
        self.assertEqual(self.balances(), {"src": 0, "dst": 120})
        self.assert_released(self.connections[0], commits=1, rollbacks=0)

    def test_invalid_arguments_do_not_open_connection(self):
        class IntSubclass(int):
            pass

        invalid = [
            ("", "dst", 1),
            ("src", "", 1),
            ("src", "src", 1),
            (None, "dst", 1),
            ("src", None, 1),
            (123, "dst", 1),
            ("src", b"dst", 1),
            ([], "dst", 1),
        ]
        invalid.extend(
            ("src", "dst", amount)
            for amount in (0, -1, True, False, 1.0, "1", None, IntSubclass(1))
        )
        for arguments in invalid:
            with self.subTest(arguments=arguments):
                with self.assertRaises(ValueError):
                    transfer(self.factory, *arguments)
        self.assertEqual(self.connections, [])
        self.assertEqual(self.balances(), {"src": 100, "dst": 20})

    def test_missing_accounts_roll_back(self):
        for src, dst, missing in (
            ("missing", "dst", "missing"),
            ("src", "missing", "missing"),
        ):
            with self.subTest(src=src, dst=dst):
                with self.assertRaises(UnknownAccount) as caught:
                    transfer(self.factory, src, dst, 30)
                self.assertEqual(caught.exception.args, (missing,))
                self.assertEqual(self.balances(), {"src": 100, "dst": 20})
                self.assert_released(self.connections[-1])

    def test_insufficient_funds_roll_back(self):
        with self.assertRaises(InsufficientFunds) as caught:
            transfer(self.factory, "src", "dst", 101)
        self.assertEqual(caught.exception.args, ("src",))
        self.assertEqual(self.balances(), {"src": 100, "dst": 20})
        self.assert_released(self.connections[0])

    def test_credit_sql_failure_rolls_back_debit(self):
        connection = self.factory()
        connection.executescript(
            "CREATE TRIGGER reject_credit BEFORE UPDATE ON accounts "
            "WHEN NEW.cents > OLD.cents BEGIN "
            "SELECT RAISE(ABORT, 'credit rejected'); END;"
        )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "credit rejected"):
            transfer(lambda: connection, "src", "dst", 30)
        self.assertEqual(self.balances(), {"src": 100, "dst": 20})
        self.assert_released(connection)

    def test_commit_failure_rolls_back_both_updates(self):
        connection = self.factory()
        error = sqlite3.OperationalError("commit failed")
        connection.commit_error = error
        with self.assertRaises(sqlite3.OperationalError) as caught:
            transfer(lambda: connection, "src", "dst", 30)
        self.assertIs(caught.exception, error)
        self.assertEqual(self.balances(), {"src": 100, "dst": 20})
        self.assert_released(connection, commits=1)

    def test_cancellation_and_unexpected_errors_roll_back_debit(self):
        for error in (
            asyncio.CancelledError(),
            KeyboardInterrupt(),
            RuntimeError("unexpected failure"),
        ):
            with self.subTest(error=type(error).__name__):
                connection = self.factory()
                connection.fail_statement = "UPDATE accounts SET cents = cents +"
                connection.execute_error = error
                with self.assertRaises(type(error)) as caught:
                    transfer(lambda: connection, "src", "dst", 30)
                self.assertIs(caught.exception, error)
                self.assertEqual(self.balances(), {"src": 100, "dst": 20})
                self.assert_released(connection)

    def test_begin_failure_releases_connection_and_propagates(self):
        connection = self.factory()
        error = sqlite3.OperationalError("begin failed")
        connection.fail_statement = "BEGIN"
        connection.execute_error = error
        with self.assertRaises(sqlite3.OperationalError) as caught:
            transfer(lambda: connection, "src", "dst", 30)
        self.assertIs(caught.exception, error)
        self.assertEqual(self.balances(), {"src": 100, "dst": 20})
        self.assert_released(connection)

    def test_factory_failure_propagates(self):
        error = sqlite3.OperationalError("connection failed")

        def failing_factory():
            raise error

        with self.assertRaises(sqlite3.OperationalError) as caught:
            transfer(failing_factory, "src", "dst", 30)
        self.assertIs(caught.exception, error)

    def test_account_ids_are_sql_data(self):
        src = "src'; DROP TABLE accounts; --"
        dst = "dst' OR 1=1 --"
        connection = self.factory()
        connection.execute("INSERT INTO accounts VALUES (?, ?)", (src, 50))
        connection.execute("INSERT INTO accounts VALUES (?, ?)", (dst, 10))
        transfer(lambda: connection, src, dst, 15)
        self.assertEqual(
            self.balances(), {"src": 100, "dst": 20, src: 35, dst: 25}
        )
        self.assert_released(connection, commits=1, rollbacks=0)


if __name__ == "__main__":
    unittest.main()
