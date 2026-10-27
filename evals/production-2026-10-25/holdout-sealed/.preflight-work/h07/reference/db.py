import sqlite3

class TransferError(ValueError):
    pass

class InsufficientFunds(TransferError):
    pass

def new_database():
    """Create the application's small ledger schema.

    The returned connection is owned by the caller, not transfer().
    """
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE accounts (id TEXT PRIMARY KEY, balance INTEGER NOT NULL);
        CREATE TABLE audit (source TEXT, destination TEXT, cents INTEGER);
        INSERT INTO accounts VALUES ('alice', 100), ('bob', 20);
    """)
    return conn

def balance(conn, account):
    row = conn.execute("SELECT balance FROM accounts WHERE id=?", (account,)).fetchone()
    if row is None:
        raise TransferError("unknown account")
    return row[0]
