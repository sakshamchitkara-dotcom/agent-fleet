class InsufficientFunds(Exception):
    pass


class Account:
    def __init__(self, owner, balance=0):
        self.owner = owner
        self.balance = balance


def transfer(src, dst, amount):
    if amount <= 0:
        raise ValueError("amount must be positive")
    src.balance -= amount
    dst.balance += amount
