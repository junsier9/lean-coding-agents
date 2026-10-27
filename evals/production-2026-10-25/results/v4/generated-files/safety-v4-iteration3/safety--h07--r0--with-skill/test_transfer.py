import sqlite3
import unittest
from unittest.mock import patch

from app import transfer
from db import InsufficientFunds, TransferError, new_database


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.conn = new_database()
        self.addCleanup(self.conn.close)

    def assert_ledger(self, alice=100, bob=20, audit=()):
        self.assertEqual(
            self.conn.execute("SELECT * FROM accounts ORDER BY id").fetchall(),
            [("alice", alice), ("bob", bob)],
        )
        self.assertEqual(self.conn.execute("SELECT * FROM audit").fetchall(), list(audit))
        self.assertFalse(self.conn.in_transaction)

    def test_success_commits_balances_and_audit(self):
        self.assertIsNone(transfer(self.conn, "alice", "bob", 40))
        self.conn.rollback()
        self.assert_ledger(60, 60, [("alice", "bob", 40)])
        transfer(self.conn, "bob", "alice", 60)
        self.assert_ledger(120, 0, [("alice", "bob", 40), ("bob", "alice", 60)])

    def test_cents_must_be_a_strict_positive_integer(self):
        for cents in (0, -1, True, False, 1.5, "10", None):
            with self.subTest(cents=cents):
                with self.assertRaises(TransferError) as error:
                    transfer(self.conn, "alice", "bob", cents)
                self.assertIs(type(error.exception), TransferError)
                self.assert_ledger()

    def test_same_account_is_rejected(self):
        with self.assertRaises(TransferError):
            transfer(self.conn, "alice", "alice", 10)
        self.assert_ledger()

    def test_both_accounts_must_exist_before_checking_funds(self):
        for source, destination in (("missing", "bob"), ("alice", "missing")):
            with self.subTest(source=source, destination=destination):
                with self.assertRaises(TransferError) as error:
                    transfer(self.conn, source, destination, 101)
                self.assertIs(type(error.exception), TransferError)
                self.assert_ledger()
        transfer(self.conn, "alice", "bob", 10)
        self.assert_ledger(90, 30, [("alice", "bob", 10)])

    def test_insufficient_funds_rolls_back(self):
        with self.assertRaises(InsufficientFunds):
            transfer(self.conn, "alice", "bob", 101)
        self.assert_ledger()
        transfer(self.conn, "alice", "bob", 100)
        self.assert_ledger(0, 120, [("alice", "bob", 100)])

    def test_existing_transaction_is_left_for_the_caller(self):
        self.conn.execute("UPDATE accounts SET balance=150 WHERE id='alice'")
        for cents in (10, 0):
            with self.subTest(cents=cents):
                with self.assertRaises(TransferError):
                    transfer(self.conn, "alice", "bob", cents)
                self.assertTrue(self.conn.in_transaction)
                self.assertEqual(
                    self.conn.execute("SELECT balance FROM accounts WHERE id='alice'").fetchone(),
                    (150,),
                )
                self.assertEqual(self.conn.execute("SELECT * FROM audit").fetchall(), [])
        self.conn.rollback()
        self.assert_ledger()
        transfer(self.conn, "alice", "bob", 10)
        self.assert_ledger(90, 30, [("alice", "bob", 10)])

    def test_credit_failure_rolls_back_debit(self):
        self.conn.execute("""
            CREATE TRIGGER block_credit BEFORE UPDATE ON accounts
            WHEN NEW.id = 'bob'
            BEGIN SELECT RAISE(ABORT, 'credit blocked'); END
        """)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "credit blocked"):
            transfer(self.conn, "alice", "bob", 10)
        self.assert_ledger()
        self.conn.execute("DROP TRIGGER block_credit")
        transfer(self.conn, "alice", "bob", 10)
        self.assert_ledger(90, 30, [("alice", "bob", 10)])

    def test_audit_failure_rolls_back_both_balances(self):
        self.conn.execute("""
            CREATE TRIGGER block_audit BEFORE INSERT ON audit
            BEGIN SELECT RAISE(ABORT, 'audit blocked'); END
        """)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "audit blocked"):
            transfer(self.conn, "alice", "bob", 10)
        self.assert_ledger()
        self.conn.execute("DROP TRIGGER block_audit")
        transfer(self.conn, "alice", "bob", 10)
        self.assert_ledger(90, 30, [("alice", "bob", 10)])

    def test_sqlite_automatic_rollback_preserves_original_error(self):
        self.conn.execute("""
            CREATE TRIGGER block_audit BEFORE INSERT ON audit
            BEGIN SELECT RAISE(ROLLBACK, 'automatic rollback'); END
        """)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "automatic rollback"):
            transfer(self.conn, "alice", "bob", 10)
        self.assert_ledger()

    def test_commit_failure_rolls_back_balances_and_audit(self):
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript("""
            CREATE TABLE approved_accounts (id TEXT PRIMARY KEY);
            DROP TABLE audit;
            CREATE TABLE audit (
                source TEXT,
                destination TEXT REFERENCES approved_accounts(id)
                    DEFERRABLE INITIALLY DEFERRED,
                cents INTEGER
            );
        """)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "FOREIGN KEY"):
            transfer(self.conn, "alice", "bob", 10)
        self.assert_ledger()
        self.conn.execute("INSERT INTO approved_accounts VALUES ('bob')")
        self.conn.commit()
        transfer(self.conn, "alice", "bob", 10)
        self.assert_ledger(90, 30, [("alice", "bob", 10)])

    def test_unexpected_errors_and_cancellation_propagate_after_rollback(self):
        for failure in (RuntimeError("unexpected failure"), KeyboardInterrupt()):
            with self.subTest(failure=type(failure).__name__):
                with patch("app.balance", side_effect=failure):
                    with self.assertRaises(type(failure)) as error:
                        transfer(self.conn, "alice", "bob", 10)
                self.assertIs(error.exception, failure)
                self.assert_ledger()

    def test_autocommit_connections_still_use_one_explicit_transaction(self):
        for setting, value in (("isolation_level", None), ("autocommit", True)):
            with self.subTest(setting=setting):
                conn = new_database()
                self.addCleanup(conn.close)
                setattr(conn, setting, value)
                transfer(conn, "alice", "bob", 10)
                conn.execute("""
                    CREATE TRIGGER block_audit BEFORE INSERT ON audit
                    BEGIN SELECT RAISE(ABORT, 'audit blocked'); END
                """)
                with self.assertRaisesRegex(sqlite3.IntegrityError, "audit blocked"):
                    transfer(conn, "alice", "bob", 5)
                self.assertFalse(conn.in_transaction)
                self.assertEqual(
                    conn.execute("SELECT * FROM accounts ORDER BY id").fetchall(),
                    [("alice", 90), ("bob", 30)],
                )
                self.assertEqual(
                    conn.execute("SELECT * FROM audit").fetchall(),
                    [("alice", "bob", 10)],
                )


if __name__ == "__main__":
    unittest.main()
