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

    def test_success_commits_and_leaves_connection_usable(self):
        transfer(self.conn, "alice", "bob", 100)
        self.assert_ledger(0, 120, [("alice", "bob", 100)])
        self.conn.rollback()
        self.assert_ledger(0, 120, [("alice", "bob", 100)])
        transfer(self.conn, "bob", "alice", 10)
        self.assert_ledger(10, 110, [("alice", "bob", 100), ("bob", "alice", 10)])

    def test_cents_must_be_a_strict_positive_integer(self):
        class IntSubclass(int):
            pass

        for cents in (0, -1, True, False, 1.0, "1", None, IntSubclass(1)):
            with self.subTest(cents=cents, kind=type(cents)):
                with self.assertRaises(TransferError):
                    transfer(self.conn, "alice", "bob", cents)
                self.assert_ledger()
        transfer(self.conn, "alice", "bob", 1)
        self.assert_ledger(99, 21, [("alice", "bob", 1)])

    def test_same_account_is_rejected(self):
        with self.assertRaises(TransferError):
            transfer(self.conn, "alice", "alice", 1)
        self.assert_ledger()

    def test_both_accounts_must_exist(self):
        for source, destination, cents in (
            ("missing", "bob", 1),
            ("alice", "missing", 1),
            ("alice", "missing", 101),
        ):
            with self.subTest(source=source, destination=destination, cents=cents):
                with self.assertRaises(TransferError) as error:
                    transfer(self.conn, source, destination, cents)
                self.assertIs(type(error.exception), TransferError)
                self.assert_ledger()
        transfer(self.conn, "alice", "bob", 1)
        self.assert_ledger(99, 21, [("alice", "bob", 1)])

    def test_insufficient_funds_rolls_back(self):
        with self.assertRaises(InsufficientFunds):
            transfer(self.conn, "alice", "bob", 101)
        self.assert_ledger()
        transfer(self.conn, "alice", "bob", 1)
        self.assert_ledger(99, 21, [("alice", "bob", 1)])

    def test_existing_transaction_and_pending_writes_are_preserved(self):
        self.conn.execute("UPDATE accounts SET balance=150 WHERE id='alice'")
        self.conn.execute("INSERT INTO audit VALUES ('caller', 'pending', 7)")
        with self.assertRaises(TransferError):
            transfer(self.conn, "alice", "bob", 10)
        self.assertTrue(self.conn.in_transaction)
        self.assertEqual(
            self.conn.execute("SELECT id, balance FROM accounts ORDER BY id").fetchall(),
            [("alice", 150), ("bob", 20)],
        )
        self.assertEqual(
            self.conn.execute("SELECT * FROM audit").fetchall(), [("caller", "pending", 7)]
        )
        self.conn.rollback()
        self.assert_ledger()
        transfer(self.conn, "alice", "bob", 1)
        self.assert_ledger(99, 21, [("alice", "bob", 1)])

    def test_existing_read_transaction_is_preserved(self):
        self.conn.execute("BEGIN")
        self.conn.execute("SELECT * FROM accounts").fetchall()
        with self.assertRaises(TransferError):
            transfer(self.conn, "alice", "bob", 10)
        self.assertTrue(self.conn.in_transaction)
        self.conn.rollback()
        self.assert_ledger()

    def test_sqlite_write_errors_roll_back_all_changes(self):
        for event, action in (
            ("BEFORE UPDATE ON accounts WHEN NEW.id='bob'", "ABORT"),
            ("BEFORE INSERT ON audit", "ABORT"),
            ("BEFORE INSERT ON audit", "ROLLBACK"),
        ):
            with self.subTest(event=event, action=action):
                self.conn.execute(
                    f"CREATE TRIGGER reject_write {event} "
                    f"BEGIN SELECT RAISE({action}, 'write rejected'); END"
                )
                with self.assertRaisesRegex(sqlite3.IntegrityError, "write rejected"):
                    transfer(self.conn, "alice", "bob", 10)
                self.assert_ledger()
                self.conn.execute("DROP TRIGGER reject_write")
        transfer(self.conn, "alice", "bob", 1)
        self.assert_ledger(99, 21, [("alice", "bob", 1)])

    def test_commit_failure_rolls_back_balances_audit_and_trigger_writes(self):
        self.conn.executescript("""
            PRAGMA foreign_keys=ON;
            CREATE TABLE audit_link (
                account TEXT REFERENCES accounts(id) DEFERRABLE INITIALLY DEFERRED
            );
            CREATE TRIGGER invalid_audit_link AFTER INSERT ON audit
            BEGIN
                INSERT INTO audit_link VALUES ('missing');
            END;
        """)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "FOREIGN KEY"):
            transfer(self.conn, "alice", "bob", 10)
        self.assert_ledger()
        self.assertEqual(self.conn.execute("SELECT * FROM audit_link").fetchall(), [])
        self.conn.execute("DROP TRIGGER invalid_audit_link")
        transfer(self.conn, "alice", "bob", 1)
        self.assert_ledger(99, 21, [("alice", "bob", 1)])

    def test_operational_error_keeps_its_type_and_rolls_back(self):
        self.conn.execute("DROP TABLE audit")
        with self.assertRaises(sqlite3.OperationalError):
            transfer(self.conn, "alice", "bob", 10)
        self.assertFalse(self.conn.in_transaction)
        self.assertEqual(
            self.conn.execute("SELECT id, balance FROM accounts ORDER BY id").fetchall(),
            [("alice", 100), ("bob", 20)],
        )
        self.conn.execute("CREATE TABLE audit (source TEXT, destination TEXT, cents INTEGER)")
        transfer(self.conn, "alice", "bob", 1)
        self.assert_ledger(99, 21, [("alice", "bob", 1)])

    def test_explicit_transactions_work_in_autocommit_modes(self):
        for attribute, value in (("isolation_level", None), ("autocommit", True)):
            with self.subTest(attribute=attribute, value=value):
                conn = new_database()
                self.addCleanup(conn.close)
                setattr(conn, attribute, value)
                conn.executescript("""
                    CREATE TRIGGER reject_audit BEFORE INSERT ON audit
                    BEGIN SELECT RAISE(ABORT, 'audit rejected'); END;
                """)
                with self.assertRaisesRegex(sqlite3.IntegrityError, "audit rejected"):
                    transfer(conn, "alice", "bob", 10)
                self.assertFalse(conn.in_transaction)
                self.assertEqual(
                    conn.execute("SELECT balance FROM accounts ORDER BY id").fetchall(),
                    [(100,), (20,)],
                )
                self.assertEqual(conn.execute("SELECT * FROM audit").fetchall(), [])
                conn.execute("DROP TRIGGER reject_audit")
                transfer(conn, "alice", "bob", 10)
                self.assertFalse(conn.in_transaction)
                self.assertEqual(
                    conn.execute("SELECT balance FROM accounts ORDER BY id").fetchall(),
                    [(90,), (30,)],
                )
                self.assertEqual(
                    conn.execute("SELECT * FROM audit").fetchall(), [("alice", "bob", 10)]
                )


if __name__ == "__main__":
    unittest.main()
