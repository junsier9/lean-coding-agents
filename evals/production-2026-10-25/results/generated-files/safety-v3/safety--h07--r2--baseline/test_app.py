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
            self.conn.execute("SELECT * FROM accounts ORDER BY id").fetchall(),
            self.conn.execute("SELECT * FROM audit ORDER BY rowid").fetchall(),
        )

    def assert_connection_reusable(self):
        self.assertFalse(self.conn.in_transaction)
        transfer(self.conn, "alice", "bob", 10)
        self.assertEqual(
            self.snapshot(),
            ([("alice", 90), ("bob", 30)], [("alice", "bob", 10)]),
        )
        self.assertFalse(self.conn.in_transaction)

    def test_success_commits_balances_and_audit(self):
        transfer(self.conn, "alice", "bob", 100)
        self.assertEqual(
            self.snapshot(),
            ([("alice", 0), ("bob", 120)], [("alice", "bob", 100)]),
        )
        self.assertFalse(self.conn.in_transaction)
        transfer(self.conn, "bob", "alice", 20)
        self.assertEqual(
            self.snapshot(),
            (
                [("alice", 20), ("bob", 100)],
                [("alice", "bob", 100), ("bob", "alice", 20)],
            ),
        )
        self.assertFalse(self.conn.in_transaction)

    def test_amount_must_be_a_strictly_positive_integer(self):
        class IntSubclass(int):
            pass

        original = self.snapshot()
        for cents in (0, -1, True, False, 1.0, "1", None, IntSubclass(1)):
            with self.subTest(cents=cents, kind=type(cents)):
                with self.assertRaises(TransferError):
                    transfer(self.conn, "alice", "bob", cents)
                self.assertEqual(self.snapshot(), original)
                self.assertFalse(self.conn.in_transaction)
        self.assert_connection_reusable()

    def test_accounts_must_differ_and_both_exist(self):
        original = self.snapshot()
        for source, destination in (
            ("alice", "alice"),
            ("missing", "bob"),
            ("alice", "missing"),
            ("missing", "also missing"),
        ):
            with self.subTest(source=source, destination=destination):
                with self.assertRaises(TransferError):
                    transfer(self.conn, source, destination, 10)
                self.assertEqual(self.snapshot(), original)
                self.assertFalse(self.conn.in_transaction)
        self.assert_connection_reusable()

    def test_insufficient_funds_preserves_ledger(self):
        original = self.snapshot()
        with self.assertRaises(InsufficientFunds):
            transfer(self.conn, "alice", "bob", 101)
        self.assertEqual(self.snapshot(), original)
        self.assert_connection_reusable()

    def test_existing_transaction_is_left_untouched(self):
        original = self.snapshot()
        self.conn.execute("BEGIN")
        self.conn.execute("UPDATE accounts SET balance=125 WHERE id='alice'")
        self.conn.execute("INSERT INTO audit VALUES ('caller', 'pending', 1)")
        pending = self.snapshot()
        for cents in (10, 0):
            with self.subTest(cents=cents):
                with self.assertRaises(TransferError):
                    transfer(self.conn, "alice", "bob", cents)
                self.assertTrue(self.conn.in_transaction)
                self.assertEqual(self.snapshot(), pending)
        self.conn.execute("ROLLBACK")
        self.assertEqual(self.snapshot(), original)
        self.assert_connection_reusable()

    def test_existing_read_only_transaction_is_left_active(self):
        original = self.snapshot()
        self.conn.execute("BEGIN")
        self.conn.execute("SELECT * FROM accounts").fetchall()
        with self.assertRaises(TransferError):
            transfer(self.conn, "alice", "bob", 10)
        self.assertTrue(self.conn.in_transaction)
        self.assertEqual(self.snapshot(), original)
        self.conn.execute("ROLLBACK")
        self.assert_connection_reusable()

    def test_account_update_errors_roll_back(self):
        original = self.snapshot()
        for account in ("alice", "bob"):
            with self.subTest(account=account):
                self.conn.execute(
                    f"""CREATE TRIGGER reject_update BEFORE UPDATE ON accounts
                        WHEN OLD.id='{account}' BEGIN
                            SELECT RAISE(ABORT, 'update rejected');
                        END"""
                )
                with self.assertRaisesRegex(sqlite3.IntegrityError, "update rejected"):
                    transfer(self.conn, "alice", "bob", 10)
                self.assertEqual(self.snapshot(), original)
                self.assertFalse(self.conn.in_transaction)
                self.conn.execute("DROP TRIGGER reject_update")
        self.assert_connection_reusable()

    def test_audit_error_rolls_back_both_updates(self):
        original = self.snapshot()
        self.conn.execute(
            """CREATE TRIGGER reject_audit BEFORE INSERT ON audit BEGIN
                   SELECT RAISE(ABORT, 'audit rejected');
               END"""
        )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "audit rejected"):
            transfer(self.conn, "alice", "bob", 10)
        self.assertEqual(self.snapshot(), original)
        self.assertFalse(self.conn.in_transaction)
        self.conn.execute("DROP TRIGGER reject_audit")
        self.assert_connection_reusable()

    def test_sqlite_automatic_rollback_preserves_original_exception(self):
        original = self.snapshot()
        self.conn.execute(
            """CREATE TRIGGER reject_audit BEFORE INSERT ON audit BEGIN
                   SELECT RAISE(ROLLBACK, 'audit rolled back');
               END"""
        )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "audit rolled back"):
            transfer(self.conn, "alice", "bob", 10)
        self.assertEqual(self.snapshot(), original)
        self.assertFalse(self.conn.in_transaction)
        self.conn.execute("DROP TRIGGER reject_audit")
        self.assert_connection_reusable()

    def test_operational_error_keeps_its_type_and_rolls_back(self):
        original_balances = self.snapshot()[0]
        self.conn.execute("DROP TABLE audit")
        with self.assertRaises(sqlite3.OperationalError):
            transfer(self.conn, "alice", "bob", 10)
        self.assertEqual(
            self.conn.execute("SELECT * FROM accounts ORDER BY id").fetchall(),
            original_balances,
        )
        self.assertFalse(self.conn.in_transaction)
        self.conn.execute("CREATE TABLE audit (source TEXT, destination TEXT, cents INTEGER)")
        self.assert_connection_reusable()

    def test_commit_error_rolls_back_balances_audit_and_trigger_writes(self):
        original = self.snapshot()
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(
            """CREATE TABLE commit_checks (
                   account TEXT REFERENCES accounts(id) DEFERRABLE INITIALLY DEFERRED
               );
               CREATE TRIGGER reject_commit AFTER INSERT ON audit BEGIN
                   INSERT INTO commit_checks VALUES ('missing');
               END;"""
        )
        with self.assertRaises(sqlite3.IntegrityError):
            transfer(self.conn, "alice", "bob", 10)
        self.assertEqual(self.snapshot(), original)
        self.assertEqual(self.conn.execute("SELECT * FROM commit_checks").fetchall(), [])
        self.assertFalse(self.conn.in_transaction)
        self.conn.execute("DROP TRIGGER reject_commit")
        self.assert_connection_reusable()


class IsolationLevelNoneTransferTests(TransferTests):
    def setUp(self):
        super().setUp()
        self.conn.isolation_level = None


class AutocommitTransferTests(TransferTests):
    def setUp(self):
        super().setUp()
        self.conn.autocommit = True


if __name__ == "__main__":
    unittest.main()
