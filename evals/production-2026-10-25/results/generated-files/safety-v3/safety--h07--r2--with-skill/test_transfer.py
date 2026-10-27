import sqlite3
import unittest
from unittest.mock import patch

from app import transfer
from db import InsufficientFunds, TransferError, new_database


def ledger(conn):
    return (
        conn.execute("SELECT id, balance FROM accounts ORDER BY id").fetchall(),
        conn.execute("SELECT source, destination, cents FROM audit ORDER BY rowid").fetchall(),
    )


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.conn = new_database()
        self.addCleanup(self.conn.close)
        self.initial = ledger(self.conn)

    def test_success_commits_and_connection_remains_usable(self):
        self.assertIsNone(transfer(self.conn, "alice", "bob", 30))
        self.assertFalse(self.conn.in_transaction)
        self.conn.rollback()
        self.assertEqual(
            ledger(self.conn),
            ([("alice", 70), ("bob", 50)], [("alice", "bob", 30)]),
        )
        transfer(self.conn, "bob", "alice", 5)
        self.assertEqual(
            ledger(self.conn),
            (
                [("alice", 75), ("bob", 45)],
                [("alice", "bob", 30), ("bob", "alice", 5)],
            ),
        )

    def test_can_transfer_entire_balance(self):
        transfer(self.conn, "alice", "bob", 100)
        self.assertEqual(
            ledger(self.conn),
            ([("alice", 0), ("bob", 120)], [("alice", "bob", 100)]),
        )
        self.assertFalse(self.conn.in_transaction)

    def test_cents_must_be_a_strictly_positive_integer(self):
        class IntSubclass(int):
            pass

        for cents in (0, -1, True, False, 1.0, "1", None, IntSubclass(1)):
            with self.subTest(cents=cents, kind=type(cents)):
                with self.assertRaises(TransferError):
                    transfer(self.conn, "alice", "bob", cents)
                self.assertEqual(ledger(self.conn), self.initial)
                self.assertFalse(self.conn.in_transaction)

    def test_accounts_must_be_distinct_and_exist(self):
        for source, destination in (
            ("alice", "alice"),
            ("missing", "bob"),
            ("alice", "missing"),
            ("missing", "missing"),
        ):
            with self.subTest(source=source, destination=destination):
                with self.assertRaises(TransferError):
                    transfer(self.conn, source, destination, 10)
                self.assertEqual(ledger(self.conn), self.initial)
                self.assertFalse(self.conn.in_transaction)

    def test_insufficient_funds_changes_nothing(self):
        with self.assertRaises(InsufficientFunds):
            transfer(self.conn, "alice", "bob", 101)
        self.assertEqual(ledger(self.conn), self.initial)
        self.assertFalse(self.conn.in_transaction)
        transfer(self.conn, "alice", "bob", 10)

    def test_caller_transaction_is_rejected_without_sql_or_state_changes(self):
        self.conn.execute("UPDATE accounts SET balance=99 WHERE id='alice'")
        self.conn.execute("INSERT INTO audit VALUES ('caller', 'bob', 1)")
        pending = ledger(self.conn)
        statements = []
        self.conn.set_trace_callback(statements.append)
        with self.assertRaises(TransferError):
            transfer(self.conn, "alice", "bob", 10)
        self.assertEqual(statements, [])
        self.conn.set_trace_callback(None)
        self.assertTrue(self.conn.in_transaction)
        self.assertEqual(ledger(self.conn), pending)
        self.conn.rollback()
        self.assertEqual(ledger(self.conn), self.initial)
        transfer(self.conn, "alice", "bob", 10)

    def test_explicit_empty_caller_transaction_is_preserved(self):
        self.conn.execute("BEGIN")
        with self.assertRaises(TransferError):
            transfer(self.conn, "alice", "bob", 10)
        self.assertTrue(self.conn.in_transaction)
        self.assertEqual(ledger(self.conn), self.initial)
        self.conn.rollback()

    def test_destination_update_failure_rolls_back_debit(self):
        self.conn.execute("""
            CREATE TRIGGER reject_credit BEFORE UPDATE ON accounts
            WHEN NEW.id='bob'
            BEGIN SELECT RAISE(ABORT, 'credit rejected'); END
        """)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "credit rejected"):
            transfer(self.conn, "alice", "bob", 30)
        self.assertFalse(self.conn.in_transaction)
        self.assertEqual(ledger(self.conn), self.initial)
        self.conn.execute("DROP TRIGGER reject_credit")
        transfer(self.conn, "alice", "bob", 30)

    def test_audit_failure_rolls_back_both_balances(self):
        self.conn.execute("""
            CREATE TRIGGER reject_audit BEFORE INSERT ON audit
            BEGIN SELECT RAISE(ABORT, 'audit rejected'); END
        """)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "audit rejected"):
            transfer(self.conn, "alice", "bob", 30)
        self.assertFalse(self.conn.in_transaction)
        self.assertEqual(ledger(self.conn), self.initial)
        self.conn.execute("DROP TRIGGER reject_audit")
        transfer(self.conn, "alice", "bob", 30)

    def test_sqlite_automatic_rollback_preserves_original_error(self):
        self.conn.execute("""
            CREATE TRIGGER reject_audit BEFORE INSERT ON audit
            BEGIN SELECT RAISE(ROLLBACK, 'audit rolled back'); END
        """)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "audit rolled back"):
            transfer(self.conn, "alice", "bob", 30)
        self.assertFalse(self.conn.in_transaction)
        self.assertEqual(ledger(self.conn), self.initial)
        self.conn.execute("DROP TRIGGER reject_audit")
        transfer(self.conn, "alice", "bob", 30)

    def test_operational_error_propagates_and_rolls_back(self):
        self.conn.execute("ALTER TABLE audit RENAME TO saved_audit")
        with self.assertRaisesRegex(sqlite3.OperationalError, "no such table: audit"):
            transfer(self.conn, "alice", "bob", 30)
        self.assertFalse(self.conn.in_transaction)
        self.conn.execute("ALTER TABLE saved_audit RENAME TO audit")
        self.assertEqual(ledger(self.conn), self.initial)
        transfer(self.conn, "alice", "bob", 30)

    def test_commit_failure_rolls_back_all_writes(self):
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("""
            CREATE TABLE audit_guard (
                account TEXT REFERENCES accounts(id) DEFERRABLE INITIALLY DEFERRED
            )
        """)
        self.conn.execute("""
            CREATE TRIGGER invalidate_audit AFTER INSERT ON audit
            BEGIN INSERT INTO audit_guard VALUES ('missing'); END
        """)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "FOREIGN KEY"):
            transfer(self.conn, "alice", "bob", 30)
        self.assertFalse(self.conn.in_transaction)
        self.assertEqual(ledger(self.conn), self.initial)
        self.assertEqual(self.conn.execute("SELECT * FROM audit_guard").fetchall(), [])
        self.conn.execute("DROP TRIGGER invalidate_audit")
        transfer(self.conn, "alice", "bob", 30)

    def test_interruption_rolls_back_and_propagates(self):
        with patch("app.balance", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                transfer(self.conn, "alice", "bob", 30)
        self.assertFalse(self.conn.in_transaction)
        self.assertEqual(ledger(self.conn), self.initial)
        transfer(self.conn, "alice", "bob", 30)

    def test_explicit_transaction_also_works_with_autocommit_connections(self):
        for mode in ("isolation_level", "autocommit"):
            with self.subTest(mode=mode):
                conn = new_database()
                self.addCleanup(conn.close)
                if mode == "isolation_level":
                    conn.isolation_level = None
                else:
                    conn.autocommit = True
                transfer(conn, "alice", "bob", 30)
                self.assertFalse(conn.in_transaction)
                committed = ledger(conn)
                self.assertEqual(
                    committed,
                    ([("alice", 70), ("bob", 50)], [("alice", "bob", 30)]),
                )
                conn.execute("""
                    CREATE TRIGGER reject_audit BEFORE INSERT ON audit
                    BEGIN SELECT RAISE(ABORT, 'audit rejected'); END
                """)
                with self.assertRaisesRegex(sqlite3.IntegrityError, "audit rejected"):
                    transfer(conn, "alice", "bob", 20)
                self.assertFalse(conn.in_transaction)
                self.assertEqual(ledger(conn), committed)
                conn.execute("DROP TRIGGER reject_audit")
                transfer(conn, "alice", "bob", 20)
                self.assertFalse(conn.in_transaction)
                self.assertEqual(
                    ledger(conn),
                    (
                        [("alice", 50), ("bob", 70)],
                        [("alice", "bob", 30), ("alice", "bob", 20)],
                    ),
                )


if __name__ == "__main__":
    unittest.main()
