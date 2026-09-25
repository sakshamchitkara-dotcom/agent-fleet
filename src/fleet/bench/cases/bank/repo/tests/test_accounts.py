import unittest

from bank.accounts import Account, InsufficientFunds, transfer


class TransferTest(unittest.TestCase):
    def test_transfer(self):
        a, b = Account("a", 100), Account("b", 0)
        transfer(a, b, 30)
        self.assertEqual((a.balance, b.balance), (70, 30))

    def test_overdraft_refused_and_nothing_moves(self):
        a, b = Account("a", 10), Account("b", 0)
        with self.assertRaises(InsufficientFunds):
            transfer(a, b, 50)
        self.assertEqual((a.balance, b.balance), (10, 0))
