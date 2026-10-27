import sqlite3
import unittest
from app import transfer
from db import new_database, balance, TransferError, InsufficientFunds

class Contract(unittest.TestCase):
    def setUp(self):
        self.conn = new_database()
        self.addCleanup(self.conn.close)

    def test_success(self):
        transfer(self.conn, "alice", "bob", 30)
        self.assertEqual((balance(self.conn, "alice"), balance(self.conn, "bob")), (70, 50))
        self.assertEqual(self.conn.execute("SELECT * FROM audit").fetchall(), [("alice", "bob", 30)])

    def test_missing_destination_rolls_back(self):
        with self.assertRaises(TransferError):
            transfer(self.conn, "alice", "missing", 10)
        self.assertEqual(balance(self.conn, "alice"), 100)
        self.assertFalse(self.conn.in_transaction)

    def test_audit_failure_rolls_back(self):
        self.conn.execute("CREATE TRIGGER no_audit BEFORE INSERT ON audit BEGIN SELECT RAISE(ABORT,'unavailable'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            transfer(self.conn, "alice", "bob", 10)
        self.assertEqual((balance(self.conn, "alice"), balance(self.conn, "bob")), (100, 20))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM audit").fetchone()[0], 0)

    def test_insufficient(self):
        with self.assertRaises(InsufficientFunds):
            transfer(self.conn, "alice", "bob", 101)
        self.assertEqual(balance(self.conn, "alice"), 100)

    def test_bad_amounts(self):
        for cents in (-1, 0, True, "5"):
            with self.subTest(cents=cents), self.assertRaises(TransferError):
                transfer(self.conn, "alice", "bob", cents)
        self.assertEqual(balance(self.conn, "alice"), 100)

    def test_same_account(self):
        with self.assertRaises(TransferError):
            transfer(self.conn, "alice", "alice", 1)

    def test_connection_owned_by_caller(self):
        transfer(self.conn, "alice", "bob", 1)
        self.assertEqual(self.conn.execute("SELECT 7").fetchone(), (7,))

    def test_existing_transaction_preserved(self):
        self.conn.execute("UPDATE accounts SET balance=120 WHERE id='alice'")
        with self.assertRaises(TransferError):
            transfer(self.conn, "alice", "bob", 10)
        self.assertTrue(self.conn.in_transaction)
        self.assertEqual(balance(self.conn, "alice"), 120)
        self.conn.rollback()
        self.assertEqual(balance(self.conn, "alice"), 100)

    def test_missing_source(self):
        with self.assertRaises(TransferError):
            transfer(self.conn, "missing", "bob", 10)
        self.assertEqual(balance(self.conn, "bob"), 20)

    def test_database_error_propagates(self):
        self.conn.execute("DROP TABLE audit")
        with self.assertRaises(sqlite3.OperationalError):
            transfer(self.conn, "alice", "bob", 1)
        self.assertEqual(balance(self.conn, "alice"), 100)
