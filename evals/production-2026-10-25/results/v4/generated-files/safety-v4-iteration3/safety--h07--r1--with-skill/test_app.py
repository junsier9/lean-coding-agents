import sqlite3
import unittest

from app import transfer
from db import InsufficientFunds, TransferError, balance, new_database


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.conn = new_database()
        self.addCleanup(self.conn.close)

    def assert_ledger(self, alice=100, bob=20, audit=()):
        self.assertEqual(
            self.conn.execute("SELECT id, balance FROM accounts ORDER BY id").fetchall(),
            [("alice", alice), ("bob", bob)],
        )
        self.assertEqual(self.conn.execute("SELECT * FROM audit").fetchall(), list(audit))
        self.assertFalse(self.conn.in_transaction)

    def assert_reusable(self):
        transfer(self.conn, "alice", "bob", 1)
        self.assert_ledger(99, 21, [("alice", "bob", 1)])

    def test_success_commits_balances_and_audit(self):
        result = transfer(
            conn=self.conn, source="alice", destination="bob", cents=30
        )
        self.assertIsNone(result)
        self.conn.rollback()
        self.assert_ledger(70, 50, [("alice", "bob", 30)])
        transfer(self.conn, "bob", "alice", 50)
        self.assert_ledger(
            120, 0, [("alice", "bob", 30), ("bob", "alice", 50)]
        )

    def test_cents_must_be_a_strict_positive_integer(self):
        class IntSubclass(int):
            pass

        for cents in (0, -1, True, False, 1.0, "1", None, IntSubclass(1)):
            with self.subTest(cents=cents, value_type=type(cents)):
                with self.assertRaises(TransferError) as caught:
                    transfer(self.conn, "alice", "bob", cents)
                self.assertIs(type(caught.exception), TransferError)
                self.assert_ledger()
        self.assert_reusable()

    def test_same_account_is_rejected(self):
        with self.assertRaises(TransferError):
            transfer(self.conn, "alice", "alice", 10)
        self.assert_ledger()
        self.assert_reusable()

    def test_both_accounts_must_exist(self):
        for source, destination, cents in (
            ("missing", "bob", 10),
            ("alice", "missing", 10),
            ("missing", "other", 10),
            ("alice", "missing", 101),
        ):
            with self.subTest(source=source, destination=destination, cents=cents):
                with self.assertRaises(TransferError) as caught:
                    transfer(self.conn, source, destination, cents)
                self.assertIs(type(caught.exception), TransferError)
                self.assert_ledger()
        self.assert_reusable()

    def test_insufficient_funds_rolls_back(self):
        with self.assertRaises(InsufficientFunds):
            transfer(self.conn, "alice", "bob", 101)
        self.assert_ledger()
        self.assert_reusable()

    def test_existing_transaction_is_untouched(self):
        self.conn.execute("UPDATE accounts SET balance=107 WHERE id='alice'")
        for source, destination, cents in (
            ("alice", "bob", 10),
            ("alice", "missing", 10),
            ("alice", "alice", 10),
            ("alice", "bob", 0),
        ):
            with self.subTest(source=source, destination=destination, cents=cents):
                statements = []
                self.conn.set_trace_callback(statements.append)
                try:
                    with self.assertRaises(TransferError) as caught:
                        transfer(self.conn, source, destination, cents)
                finally:
                    self.conn.set_trace_callback(None)
                self.assertIs(type(caught.exception), TransferError)
                self.assertEqual(statements, [])
                self.assertTrue(self.conn.in_transaction)
                self.assertEqual(balance(self.conn, "alice"), 107)
        self.conn.rollback()
        self.assert_ledger()
        self.assert_reusable()

    def test_sqlite_statement_errors_roll_back_every_step(self):
        for event, condition in (
            ("UPDATE ON accounts", "OLD.id='alice'"),
            ("UPDATE ON accounts", "OLD.id='bob'"),
            ("INSERT ON audit", "1"),
        ):
            with self.subTest(event=event, condition=condition):
                self.conn.execute(
                    f"CREATE TRIGGER reject_transfer BEFORE {event} "
                    f"WHEN {condition} BEGIN SELECT RAISE(ABORT, 'blocked'); END"
                )
                with self.assertRaisesRegex(sqlite3.IntegrityError, "blocked"):
                    transfer(self.conn, "alice", "bob", 10)
                self.assert_ledger()
                self.conn.execute("DROP TRIGGER reject_transfer")
        self.assert_reusable()

    def test_sqlite_automatic_rollback_preserves_original_error(self):
        self.conn.execute(
            "CREATE TRIGGER reject_transfer BEFORE INSERT ON audit "
            "BEGIN SELECT RAISE(ROLLBACK, 'automatic rollback'); END"
        )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "automatic rollback"):
            transfer(self.conn, "alice", "bob", 10)
        self.assert_ledger()
        self.conn.execute("DROP TRIGGER reject_transfer")
        self.assert_reusable()

    def test_failed_commit_rolls_back_balances_and_audit(self):
        self.conn.executescript(
            """
            PRAGMA foreign_keys=ON;
            CREATE TABLE approved (cents INTEGER PRIMARY KEY);
            CREATE TABLE pending (
                cents INTEGER REFERENCES approved(cents) DEFERRABLE INITIALLY DEFERRED
            );
            CREATE TRIGGER check_audit AFTER INSERT ON audit
            BEGIN INSERT INTO pending VALUES (NEW.cents); END;
            """
        )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "FOREIGN KEY"):
            transfer(self.conn, "alice", "bob", 10)
        self.assert_ledger()
        self.assertEqual(self.conn.execute("SELECT * FROM pending").fetchall(), [])
        self.conn.execute("DROP TRIGGER check_audit")
        self.assert_reusable()

    def test_sqlite_read_errors_propagate(self):
        self.conn.execute("DROP TABLE accounts")
        with self.assertRaises(sqlite3.OperationalError):
            transfer(self.conn, "alice", "bob", 10)
        self.assertFalse(self.conn.in_transaction)
        self.assertEqual(self.conn.execute("SELECT 1").fetchone(), (1,))

    def test_autocommit_connections_commit_and_roll_back(self):
        for setting in ("isolation_level", "autocommit"):
            with self.subTest(setting=setting):
                setattr(self.conn, setting, None if setting == "isolation_level" else True)
                self.conn.execute(
                    "CREATE TRIGGER reject_transfer BEFORE INSERT ON audit "
                    "BEGIN SELECT RAISE(ABORT, 'blocked'); END"
                )
                with self.assertRaisesRegex(sqlite3.IntegrityError, "blocked"):
                    transfer(self.conn, "alice", "bob", 10)
                self.assert_ledger()
                self.conn.execute("DROP TRIGGER reject_transfer")
                self.assert_reusable()
                self.conn.execute("UPDATE accounts SET balance=100 WHERE id='alice'")
                self.conn.execute("UPDATE accounts SET balance=20 WHERE id='bob'")
                self.conn.execute("DELETE FROM audit")


if __name__ == "__main__":
    unittest.main()
