import sqlite3
import unittest

from app import transfer
from db import InsufficientFunds, TransferError, new_database


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.conn = new_database()
        self.addCleanup(self.conn.close)

    def snapshot(self):
        return (
            self.conn.execute("SELECT id, balance FROM accounts ORDER BY id").fetchall(),
            self.conn.execute("SELECT source, destination, cents FROM audit").fetchall(),
        )

    def assert_rejected(self, exception_type, source, destination, cents):
        before = self.snapshot()
        with self.assertRaises(exception_type) as caught:
            transfer(self.conn, source, destination, cents)
        self.assertIs(type(caught.exception), exception_type)
        self.assertFalse(self.conn.in_transaction)
        self.assertEqual(self.snapshot(), before)

    def assert_success(self):
        transfer(self.conn, "alice", "bob", 30)
        self.assertFalse(self.conn.in_transaction)
        self.assertEqual(
            self.snapshot(),
            ([("alice", 70), ("bob", 50)], [("alice", "bob", 30)]),
        )
        # A second write proves that the caller still owns a usable connection.
        transfer(self.conn, "bob", "alice", 10)
        self.assertFalse(self.conn.in_transaction)
        self.assertEqual(
            self.snapshot(),
            (
                [("alice", 80), ("bob", 40)],
                [("alice", "bob", 30), ("bob", "alice", 10)],
            ),
        )

    def test_success_and_connection_reuse(self):
        self.assert_success()

    def test_success_with_legacy_autocommit(self):
        self.conn.isolation_level = None
        self.assert_success()

    def test_success_with_explicit_autocommit(self):
        self.conn.autocommit = True
        self.assert_success()

    def test_cents_must_be_a_strictly_positive_builtin_int(self):
        class IntSubclass(int):
            pass

        for cents in (0, -1, True, False, 1.0, "1", None, IntSubclass(1)):
            with self.subTest(cents=cents, kind=type(cents).__name__):
                self.assert_rejected(TransferError, "alice", "bob", cents)
        self.assert_success()

    def test_accounts_must_exist_and_differ(self):
        for source, destination in (
            ("alice", "alice"),
            ("alice", "missing"),
            ("missing", "bob"),
            ("missing", "missing"),
            ("alice", None),
        ):
            with self.subTest(source=source, destination=destination):
                self.assert_rejected(TransferError, source, destination, 1)
        self.assert_success()

    def test_sqlite_aliases_of_one_account_are_rejected(self):
        self.conn.execute("INSERT INTO accounts VALUES ('1', 100)")
        self.conn.commit()
        self.assert_rejected(TransferError, 1, "1", 10)

    def test_missing_destination_is_checked_before_insufficient_balance(self):
        self.assert_rejected(TransferError, "alice", "missing", 101)

    def test_insufficient_funds(self):
        self.assert_rejected(InsufficientFunds, "alice", "bob", 101)
        self.assert_success()

    def test_exact_balance_can_be_transferred(self):
        transfer(self.conn, "alice", "bob", 100)
        self.assertFalse(self.conn.in_transaction)
        self.assertEqual(
            self.snapshot(),
            ([("alice", 0), ("bob", 120)], [("alice", "bob", 100)]),
        )

    def test_balance_update_failures_roll_back(self):
        for account in ("alice", "bob"):
            with self.subTest(account=account):
                self.conn.execute(
                    f"""CREATE TRIGGER reject_update BEFORE UPDATE ON accounts
                    WHEN OLD.id = '{account}'
                    BEGIN SELECT RAISE(ABORT, 'update rejected'); END"""
                )
                self.assert_rejected(sqlite3.IntegrityError, "alice", "bob", 30)
                self.conn.execute("DROP TRIGGER reject_update")
        self.assert_success()

    def test_audit_failures_roll_back_in_every_autocommit_mode(self):
        for mode in ("default", "legacy", "explicit"):
            for action in ("ABORT", "ROLLBACK"):
                with self.subTest(mode=mode, action=action):
                    self.conn.autocommit = sqlite3.LEGACY_TRANSACTION_CONTROL
                    self.conn.isolation_level = None if mode == "legacy" else ""
                    if mode == "explicit":
                        self.conn.autocommit = True
                    self.conn.execute(
                        f"""CREATE TRIGGER reject_audit BEFORE INSERT ON audit
                        BEGIN SELECT RAISE({action}, 'audit rejected'); END"""
                    )
                    self.assert_rejected(sqlite3.IntegrityError, "alice", "bob", 30)
                    self.conn.execute("DROP TRIGGER reject_audit")
        self.assert_success()

    def test_missing_audit_table_preserves_operational_error(self):
        self.conn.execute("DROP TABLE audit")
        before = self.conn.execute("SELECT * FROM accounts ORDER BY id").fetchall()
        with self.assertRaises(sqlite3.OperationalError) as caught:
            transfer(self.conn, "alice", "bob", 30)
        self.assertIs(type(caught.exception), sqlite3.OperationalError)
        self.assertFalse(self.conn.in_transaction)
        self.assertEqual(
            self.conn.execute("SELECT * FROM accounts ORDER BY id").fetchall(), before
        )
        self.conn.execute("CREATE TABLE audit (source TEXT, destination TEXT, cents INTEGER)")
        self.assert_success()

    def test_begin_and_commit_failures_preserve_sqlite_error(self):
        for denied_operation in ("BEGIN", "COMMIT"):
            with self.subTest(operation=denied_operation):
                def authorize(action, operation, _table, _database, _trigger):
                    if action == sqlite3.SQLITE_TRANSACTION and operation == denied_operation:
                        return sqlite3.SQLITE_DENY
                    return sqlite3.SQLITE_OK

                self.conn.set_authorizer(authorize)
                try:
                    self.assert_rejected(sqlite3.DatabaseError, "alice", "bob", 30)
                finally:
                    self.conn.set_authorizer(None)
        self.assert_success()

    def test_existing_transaction_is_untouched(self):
        for finish in ("COMMIT", "ROLLBACK"):
            with self.subTest(finish=finish):
                before = self.snapshot()
                self.conn.execute("BEGIN")
                self.conn.execute("UPDATE accounts SET balance=balance+7 WHERE id='alice'")
                self.conn.execute("INSERT INTO audit VALUES ('caller', 'alice', 7)")
                pending = self.snapshot()
                statements = []
                self.conn.set_trace_callback(statements.append)
                try:
                    for cents in (10, 0):
                        with self.assertRaises(TransferError) as caught:
                            transfer(self.conn, "alice", "bob", cents)
                        self.assertIs(type(caught.exception), TransferError)
                    self.assertEqual(statements, [])
                finally:
                    self.conn.set_trace_callback(None)
                self.assertTrue(self.conn.in_transaction)
                self.assertEqual(self.snapshot(), pending)
                self.conn.execute(finish)
                self.assertEqual(self.snapshot(), pending if finish == "COMMIT" else before)

    def test_existing_transaction_without_writes_is_untouched(self):
        self.conn.execute("BEGIN")
        with self.assertRaises(TransferError):
            transfer(self.conn, "alice", "bob", 30)
        self.assertTrue(self.conn.in_transaction)
        self.conn.rollback()
        self.assert_success()

    def test_autocommit_false_preserves_callers_implicit_transaction(self):
        self.conn.autocommit = False
        self.conn.execute("UPDATE accounts SET balance=balance+7 WHERE id='alice'")
        pending = self.snapshot()
        with self.assertRaises(TransferError):
            transfer(self.conn, "alice", "bob", 30)
        self.assertTrue(self.conn.in_transaction)
        self.assertEqual(self.snapshot(), pending)
        self.conn.rollback()
        self.assertEqual(self.snapshot(), ([("alice", 100), ("bob", 20)], []))


if __name__ == "__main__":
    unittest.main()
