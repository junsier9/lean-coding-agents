import sqlite3
import unittest

from app import transfer
from db import InsufficientFunds, TransferError, new_database


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

    def test_success_commits_and_connection_can_be_reused(self):
        self.assertIsNone(transfer(self.conn, "alice", "bob", 100))
        self.assert_ledger(0, 120, [("alice", "bob", 100)])
        transfer(self.conn, "bob", "alice", 5)
        self.assert_ledger(5, 115, [("alice", "bob", 100), ("bob", "alice", 5)])

    def test_cents_must_be_a_strict_positive_integer(self):
        class IntSubclass(int):
            pass

        for cents in (0, -1, True, False, 1.0, "1", None, IntSubclass(1)):
            with self.subTest(cents=cents, kind=type(cents)):
                with self.assertRaises(TransferError):
                    transfer(self.conn, "alice", "bob", cents)
                self.assert_ledger()

    def test_accounts_must_be_distinct_and_exist(self):
        for source, destination in (
            ("alice", "alice"),
            ("missing", "bob"),
            ("alice", "missing"),
            ("missing", "missing"),
        ):
            with self.subTest(source=source, destination=destination):
                with self.assertRaises(TransferError) as error:
                    transfer(self.conn, source, destination, 10)
                self.assertIs(type(error.exception), TransferError)
                self.assert_ledger()

    def test_insufficient_funds_rolls_back_and_connection_can_be_reused(self):
        with self.assertRaises(InsufficientFunds):
            transfer(self.conn, "alice", "bob", 101)
        self.assert_ledger()
        transfer(self.conn, "alice", "bob", 1)
        self.assert_ledger(99, 21, [("alice", "bob", 1)])

    def test_existing_transaction_is_untouched(self):
        for cents in (10, 0):
            with self.subTest(cents=cents):
                self.conn.execute("UPDATE accounts SET balance=77 WHERE id='alice'")
                self.conn.execute("INSERT INTO audit VALUES ('caller', 'alice', 23)")
                with self.assertRaises(TransferError):
                    transfer(self.conn, "alice", "bob", cents)
                self.assertTrue(self.conn.in_transaction)
                self.assertEqual(
                    self.conn.execute("SELECT balance FROM accounts WHERE id='alice'").fetchone(),
                    (77,),
                )
                self.assertEqual(
                    self.conn.execute("SELECT * FROM audit").fetchall(),
                    [("caller", "alice", 23)],
                )
                self.conn.rollback()
                self.assert_ledger()

    def test_sqlite_failures_roll_back_all_writes_and_keep_their_type(self):
        for event, action in (
            ("BEFORE UPDATE ON accounts WHEN OLD.id='bob'", "ABORT"),
            ("BEFORE INSERT ON audit", "ABORT"),
            ("BEFORE INSERT ON audit", "ROLLBACK"),
        ):
            with self.subTest(event=event, action=action):
                self.conn.execute(
                    f"CREATE TRIGGER reject_transfer {event} "
                    f"BEGIN SELECT RAISE({action}, 'write rejected'); END"
                )
                with self.assertRaisesRegex(sqlite3.IntegrityError, "write rejected"):
                    transfer(self.conn, "alice", "bob", 10)
                self.assert_ledger()
                self.conn.execute("DROP TRIGGER reject_transfer")
        transfer(self.conn, "alice", "bob", 10)
        self.assert_ledger(90, 30, [("alice", "bob", 10)])

    def test_operational_error_is_propagated(self):
        self.conn.execute("ALTER TABLE audit RENAME TO saved_audit")
        with self.assertRaisesRegex(sqlite3.OperationalError, "no such table: audit"):
            transfer(self.conn, "alice", "bob", 10)
        self.conn.execute("ALTER TABLE saved_audit RENAME TO audit")
        self.assert_ledger()

    def test_commit_failure_rolls_back_balances_and_audit(self):
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("ALTER TABLE audit RENAME TO original_audit")
        self.conn.execute(
            "CREATE TABLE audit (source TEXT, destination TEXT, cents INTEGER, "
            "FOREIGN KEY (cents) REFERENCES accounts(id) DEFERRABLE INITIALLY DEFERRED)"
        )
        with self.assertRaises(sqlite3.IntegrityError):
            transfer(self.conn, "alice", "bob", 10)
        self.assert_ledger()
        self.conn.execute("DROP TABLE audit")
        self.conn.execute("ALTER TABLE original_audit RENAME TO audit")
        transfer(self.conn, "alice", "bob", 10)
        self.assert_ledger(90, 30, [("alice", "bob", 10)])

    def test_explicit_transactions_work_in_autocommit_modes(self):
        for setting, value in (("isolation_level", None), ("autocommit", True)):
            with self.subTest(setting=setting):
                setattr(self.conn, setting, value)
                transfer(self.conn, "alice", "bob", 10)
                self.assert_ledger(90, 30, [("alice", "bob", 10)])
                self.conn.execute(
                    "CREATE TRIGGER reject_audit BEFORE INSERT ON audit "
                    "BEGIN SELECT RAISE(ABORT, 'audit rejected'); END"
                )
                with self.assertRaises(sqlite3.IntegrityError):
                    transfer(self.conn, "alice", "bob", 10)
                self.assert_ledger(90, 30, [("alice", "bob", 10)])
                self.conn.execute("DROP TRIGGER reject_audit")
                transfer(self.conn, "bob", "alice", 10)
                self.conn.execute("DELETE FROM audit")


if __name__ == "__main__":
    unittest.main()
