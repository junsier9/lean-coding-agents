import sqlite3
import unittest

from app import transfer
from db import InsufficientFunds, TransferError, new_database


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.conn = new_database()
        self.addCleanup(self.conn.close)

    def ledger(self):
        return (
            self.conn.execute("SELECT * FROM accounts ORDER BY id").fetchall(),
            self.conn.execute("SELECT * FROM audit ORDER BY rowid").fetchall(),
        )

    def assert_idle_and_usable(self):
        self.assertFalse(self.conn.in_transaction)
        self.assertEqual(self.conn.execute("SELECT 1").fetchone(), (1,))

    def assert_sqlite_failure_rolled_back(self, trigger, message):
        self.conn.execute(trigger)
        before = self.ledger()
        with self.assertRaisesRegex(sqlite3.IntegrityError, message):
            transfer(self.conn, "alice", "bob", 30)
        self.assertEqual(self.ledger(), before)
        self.assert_idle_and_usable()
        self.conn.execute("DROP TRIGGER reject_transfer")
        transfer(self.conn, "alice", "bob", 1)
        self.assertEqual(
            self.ledger(),
            ([("alice", 99), ("bob", 21)], [("alice", "bob", 1)]),
        )
        self.assert_idle_and_usable()

    def test_success_commits_balances_and_audit_and_allows_another_transfer(self):
        transfer(self.conn, "alice", "bob", 35)
        self.assertEqual(
            self.ledger(),
            ([("alice", 65), ("bob", 55)], [("alice", "bob", 35)]),
        )
        self.assert_idle_and_usable()
        transfer(self.conn, "bob", "alice", 5)
        self.assertEqual(
            self.ledger(),
            (
                [("alice", 70), ("bob", 50)],
                [("alice", "bob", 35), ("bob", "alice", 5)],
            ),
        )
        self.assert_idle_and_usable()

    def test_entire_balance_can_be_transferred(self):
        transfer(self.conn, "alice", "bob", 100)
        self.assertEqual(
            self.ledger(),
            ([("alice", 0), ("bob", 120)], [("alice", "bob", 100)]),
        )
        self.assert_idle_and_usable()

    def test_cents_must_be_a_strictly_positive_builtin_integer(self):
        class IntegerSubclass(int):
            pass

        before = self.ledger()
        for cents in (True, False, 0, -1, 1.0, "1", None, IntegerSubclass(1)):
            with self.subTest(cents=cents):
                with self.assertRaises(TransferError) as caught:
                    transfer(self.conn, "alice", "bob", cents)
                self.assertIs(type(caught.exception), TransferError)
                self.assertEqual(self.ledger(), before)
                self.assert_idle_and_usable()
        transfer(self.conn, "alice", "bob", 1)
        self.assert_idle_and_usable()

    def test_same_account_is_rejected(self):
        before = self.ledger()
        with self.assertRaises(TransferError) as caught:
            transfer(self.conn, "alice", "alice", 1)
        self.assertIs(type(caught.exception), TransferError)
        self.assertEqual(self.ledger(), before)
        self.assert_idle_and_usable()

    def test_both_accounts_must_exist_before_checking_funds(self):
        before = self.ledger()
        for source, destination, cents in (
            ("missing", "bob", 1),
            ("alice", "missing", 1),
            ("missing", "also_missing", 1),
            ("alice", "missing", 101),
        ):
            with self.subTest(source=source, destination=destination, cents=cents):
                with self.assertRaises(TransferError) as caught:
                    transfer(self.conn, source, destination, cents)
                self.assertIs(type(caught.exception), TransferError)
                self.assertEqual(self.ledger(), before)
                self.assert_idle_and_usable()
        transfer(self.conn, "alice", "bob", 1)
        self.assert_idle_and_usable()

    def test_insufficient_funds_rolls_back_and_connection_can_transfer_again(self):
        before = self.ledger()
        with self.assertRaises(InsufficientFunds):
            transfer(self.conn, "alice", "bob", 101)
        self.assertEqual(self.ledger(), before)
        self.assert_idle_and_usable()
        transfer(self.conn, "alice", "bob", 1)
        self.assert_idle_and_usable()

    def test_caller_transaction_is_untouched_and_can_be_rolled_back(self):
        before = self.ledger()
        self.conn.execute("UPDATE accounts SET balance=150 WHERE id='alice'")
        pending = self.ledger()
        statements = []
        self.conn.set_trace_callback(statements.append)
        try:
            with self.assertRaises(TransferError) as caught:
                transfer(self.conn, "alice", "bob", 30)
        finally:
            self.conn.set_trace_callback(None)
        self.assertIs(type(caught.exception), TransferError)
        self.assertEqual(statements, [])
        self.assertTrue(self.conn.in_transaction)
        self.assertEqual(self.ledger(), pending)
        self.conn.rollback()
        self.assertEqual(self.ledger(), before)
        self.assert_idle_and_usable()

    def test_caller_transaction_can_still_be_committed_after_rejection(self):
        self.conn.execute("UPDATE accounts SET balance=150 WHERE id='alice'")
        with self.assertRaises(TransferError):
            transfer(self.conn, "alice", "bob", 30)
        self.assertTrue(self.conn.in_transaction)
        self.conn.commit()
        self.assertEqual(self.ledger(), ([("alice", 150), ("bob", 20)], []))
        self.assert_idle_and_usable()

    def test_debit_failure_rolls_back(self):
        self.assert_sqlite_failure_rolled_back(
            """CREATE TRIGGER reject_transfer BEFORE UPDATE ON accounts
               WHEN OLD.id = 'alice'
               BEGIN SELECT RAISE(ABORT, 'debit rejected'); END""",
            "debit rejected",
        )

    def test_credit_failure_rolls_back_debit(self):
        self.assert_sqlite_failure_rolled_back(
            """CREATE TRIGGER reject_transfer BEFORE UPDATE ON accounts
               WHEN OLD.id = 'bob'
               BEGIN SELECT RAISE(ABORT, 'credit rejected'); END""",
            "credit rejected",
        )

    def test_audit_failure_rolls_back_both_balances(self):
        self.assert_sqlite_failure_rolled_back(
            """CREATE TRIGGER reject_transfer BEFORE INSERT ON audit
               BEGIN SELECT RAISE(ABORT, 'audit rejected'); END""",
            "audit rejected",
        )

    def test_sqlite_automatic_rollback_keeps_original_exception(self):
        self.assert_sqlite_failure_rolled_back(
            """CREATE TRIGGER reject_transfer BEFORE INSERT ON audit
               BEGIN SELECT RAISE(ROLLBACK, 'audit rolled back'); END""",
            "audit rolled back",
        )

    def test_commit_failure_rolls_back_balances_and_audit(self):
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("CREATE TABLE approved_sources (id TEXT PRIMARY KEY)")
        self.conn.execute("DROP TABLE audit")
        self.conn.execute(
            """CREATE TABLE audit (
                source TEXT REFERENCES approved_sources(id)
                    DEFERRABLE INITIALLY DEFERRED,
                destination TEXT, cents INTEGER
            )"""
        )
        before = self.ledger()
        with self.assertRaises(sqlite3.IntegrityError):
            transfer(self.conn, "alice", "bob", 30)
        self.assertEqual(self.ledger(), before)
        self.assert_idle_and_usable()
        self.conn.execute("INSERT INTO approved_sources VALUES ('alice')")
        self.conn.commit()
        transfer(self.conn, "alice", "bob", 1)
        self.assert_idle_and_usable()

    def test_operational_error_is_propagated_and_balances_are_rolled_back(self):
        self.conn.execute("DROP TABLE audit")
        with self.assertRaises(sqlite3.OperationalError):
            transfer(self.conn, "alice", "bob", 30)
        self.assertEqual(
            self.conn.execute("SELECT * FROM accounts ORDER BY id").fetchall(),
            [("alice", 100), ("bob", 20)],
        )
        self.assert_idle_and_usable()

    def test_explicit_transactions_work_with_isolation_level_none(self):
        self.conn.isolation_level = None
        self.test_success_commits_balances_and_audit_and_allows_another_transfer()

    def test_failure_rolls_back_with_isolation_level_none(self):
        self.conn.isolation_level = None
        self.test_audit_failure_rolls_back_both_balances()

    def test_explicit_transactions_work_with_autocommit_true(self):
        self.conn.autocommit = True
        self.test_success_commits_balances_and_audit_and_allows_another_transfer()

    def test_failure_rolls_back_with_autocommit_true(self):
        self.conn.autocommit = True
        self.test_audit_failure_rolls_back_both_balances()


if __name__ == "__main__":
    unittest.main()
