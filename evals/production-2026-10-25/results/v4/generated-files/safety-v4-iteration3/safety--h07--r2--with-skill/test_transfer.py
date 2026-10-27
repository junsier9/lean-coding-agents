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
            self.conn.execute("SELECT * FROM audit ORDER BY rowid").fetchall(),
        )

    def test_success_commits_balances_and_audit_and_allows_another_transfer(self):
        self.assertIsNone(transfer(self.conn, "alice", "bob", 100))
        self.assertFalse(self.conn.in_transaction)
        self.conn.rollback()
        self.assertEqual(
            self.snapshot(),
            ([("alice", 0), ("bob", 120)], [("alice", "bob", 100)]),
        )
        transfer(conn=self.conn, source="bob", destination="alice", cents=20)
        self.assertEqual(
            self.snapshot(),
            ([("alice", 20), ("bob", 100)],
             [("alice", "bob", 100), ("bob", "alice", 20)]),
        )
        self.assertFalse(self.conn.in_transaction)

    def test_invalid_inputs_leave_database_unchanged(self):
        class IntSubclass(int):
            pass

        initial = self.snapshot()
        cases = [("alice", "bob", amount) for amount in
                 (0, -1, True, False, 1.0, "1", None, IntSubclass(1))]
        cases += [
            ("alice", "alice", 1),
            ("missing", "bob", 1),
            ("alice", "missing", 1),
            ("missing", "also-missing", 1),
        ]
        for source, destination, cents in cases:
            with self.subTest(source=source, destination=destination, cents=cents):
                with self.assertRaises(TransferError):
                    transfer(self.conn, source, destination, cents)
                self.assertEqual(self.snapshot(), initial)
                self.assertFalse(self.conn.in_transaction)
        transfer(self.conn, "alice", "bob", 1)

    def test_insufficient_funds_rolls_back_and_connection_remains_usable(self):
        initial = self.snapshot()
        with self.assertRaises(InsufficientFunds):
            transfer(self.conn, "alice", "bob", 101)
        self.assertEqual(self.snapshot(), initial)
        self.assertFalse(self.conn.in_transaction)
        transfer(self.conn, "alice", "bob", 1)

    def test_existing_transaction_is_preserved_even_for_invalid_arguments(self):
        self.conn.execute("UPDATE accounts SET balance=101 WHERE id='alice'")
        self.conn.execute("INSERT INTO audit VALUES ('pending', 'bob', 1)")
        pending = self.snapshot()
        for cents in (1, 0):
            with self.subTest(cents=cents):
                with self.assertRaises(TransferError):
                    transfer(self.conn, "alice", "bob", cents)
                self.assertTrue(self.conn.in_transaction)
                self.assertEqual(self.snapshot(), pending)
        self.conn.rollback()
        self.assertEqual(self.snapshot(), ([("alice", 100), ("bob", 20)], []))

    def test_statement_failures_roll_back_and_keep_sqlite_error(self):
        initial = self.snapshot()
        failures = [
            ("BEFORE UPDATE ON accounts WHEN NEW.id='bob'", "ABORT"),
            ("BEFORE INSERT ON audit", "ABORT"),
            ("BEFORE INSERT ON audit", "ROLLBACK"),
        ]
        for event, action in failures:
            with self.subTest(event=event, action=action):
                self.conn.execute(
                    f"CREATE TRIGGER reject_transfer {event} "
                    f"BEGIN SELECT RAISE({action}, 'transfer rejected'); END"
                )
                with self.assertRaisesRegex(sqlite3.IntegrityError, "transfer rejected"):
                    transfer(self.conn, "alice", "bob", 1)
                self.assertEqual(self.snapshot(), initial)
                self.assertFalse(self.conn.in_transaction)
                self.conn.execute("DROP TRIGGER reject_transfer")
        transfer(self.conn, "alice", "bob", 1)

    def test_commit_failure_rolls_back_balances_audit_and_trigger_side_effects(self):
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript("""
            CREATE TABLE audit_guard (
                account TEXT REFERENCES accounts(id) DEFERRABLE INITIALLY DEFERRED
            );
            CREATE TRIGGER check_audit AFTER INSERT ON audit
            BEGIN INSERT INTO audit_guard VALUES ('missing'); END;
        """)
        initial = self.snapshot()
        with self.assertRaises(sqlite3.IntegrityError):
            transfer(self.conn, "alice", "bob", 1)
        self.assertEqual(self.snapshot(), initial)
        self.assertEqual(self.conn.execute("SELECT * FROM audit_guard").fetchall(), [])
        self.assertFalse(self.conn.in_transaction)
        self.conn.execute("DROP TRIGGER check_audit")
        transfer(self.conn, "alice", "bob", 1)

    def test_explicit_transaction_works_with_autocommit_connections(self):
        for mode in ("isolation_level", "autocommit"):
            with self.subTest(mode=mode):
                conn = new_database()
                self.addCleanup(conn.close)
                if mode == "isolation_level":
                    conn.isolation_level = None
                else:
                    conn.autocommit = True
                transfer(conn, "alice", "bob", 10)
                self.assertFalse(conn.in_transaction)
                conn.execute("""
                    CREATE TRIGGER reject_audit BEFORE INSERT ON audit
                    BEGIN SELECT RAISE(ABORT, 'audit rejected'); END
                """)
                with self.assertRaisesRegex(sqlite3.IntegrityError, "audit rejected"):
                    transfer(conn, "alice", "bob", 1)
                self.assertFalse(conn.in_transaction)
                self.assertEqual(
                    conn.execute("SELECT id, balance FROM accounts ORDER BY id").fetchall(),
                    [("alice", 90), ("bob", 30)],
                )
                self.assertEqual(
                    conn.execute("SELECT * FROM audit").fetchall(),
                    [("alice", "bob", 10)],
                )
                conn.execute("BEGIN")
                with self.assertRaises(TransferError):
                    transfer(conn, "alice", "bob", 1)
                self.assertTrue(conn.in_transaction)
                conn.execute("ROLLBACK")
                conn.execute("DROP TRIGGER reject_audit")
                transfer(conn, "alice", "bob", 1)


if __name__ == "__main__":
    unittest.main()
