# Copyright (c) 2026, Michael Bockhoff GmbH and contributors
# See license.txt

import unittest
from types import SimpleNamespace

from gravityzone_billing.sync import _is_large_jump


def _settings(percent=50, min_seats=5):
	return SimpleNamespace(review_threshold_percent=percent, review_threshold_min_seats=min_seats)


class TestLargeJumpGuard(unittest.TestCase):
	"""Both thresholds must be exceeded (AND, not OR) — a big account making a
	small relative change should never be flagged just because the absolute
	seat delta looks large.
	"""

	def test_large_account_small_relative_change_is_not_flagged(self):
		# 500 -> 506: +6 seats (over the 5-seat floor) but only +1.2% (far under 50%)
		self.assertFalse(_is_large_jump(500, 506, _settings()))

	def test_small_account_small_absolute_change_is_not_flagged(self):
		# 1 -> 2 is +100%, but only 1 seat (under the 5-seat floor)
		self.assertFalse(_is_large_jump(1, 2, _settings()))

	def test_small_account_large_relative_and_absolute_change_is_flagged(self):
		# 7 -> 50: +43 seats and +614%, exceeds both
		self.assertTrue(_is_large_jump(7, 50, _settings()))

	def test_large_account_large_relative_change_is_flagged(self):
		# 500 -> 1000: +500 seats and +100%, exceeds both
		self.assertTrue(_is_large_jump(500, 1000, _settings()))

	def test_decrease_is_evaluated_the_same_way(self):
		# 100 -> 10: -90 seats, -90%, exceeds both -> flagged (a big drop is
		# just as worth a human look as a big increase, e.g. before crediting
		# an invoice)
		self.assertTrue(_is_large_jump(100, 10, _settings()))

	def test_zero_baseline_uses_absolute_count_only(self):
		# baseline 0 has no meaningful percentage; treat any qty above the
		# seat floor as needing review
		self.assertTrue(_is_large_jump(0, 10, _settings()))
		self.assertFalse(_is_large_jump(0, 3, _settings()))
