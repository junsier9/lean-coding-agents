from db import TransferError, InsufficientFunds, balance

def transfer(conn, source, destination, cents):
    if type(cents) is not int or cents <= 0:
        raise TransferError("cents")
    if source == destination:
        raise TransferError("same account")
    if balance(conn, source) < cents:
        raise InsufficientFunds("balance")
    conn.execute("UPDATE accounts SET balance=balance-? WHERE id=?", (cents, source))
    conn.commit()
    balance(conn, destination)
    conn.execute("UPDATE accounts SET balance=balance+? WHERE id=?", (cents, destination))
    conn.execute("INSERT INTO audit VALUES (?,?,?)", (source, destination, cents))
    conn.commit()
