# Copyright (c) 2026, Michael Bockhoff GmbH and contributors
# See license.txt

import unittest

from gravityzone_billing.customer_data import build_contact, parse_address


class TestParseAddress(unittest.TestCase):
	def test_german_address_with_comma(self):
		self.assertEqual(
			parse_address("Musterstr. 12, 48599 Gronau", "DE"),
			{"address_line1": "Musterstr. 12", "pincode": "48599", "city": "Gronau"},
		)

	def test_german_address_without_comma_and_on_two_lines(self):
		expected = {"address_line1": "Beispielweg 3a", "pincode": "10115", "city": "Berlin"}
		self.assertEqual(parse_address("Beispielweg 3a 10115 Berlin", "DE"), expected)
		self.assertEqual(parse_address("Beispielweg 3a\n10115 Berlin", "DE"), expected)

	def test_austrian_four_digit_zip(self):
		self.assertEqual(
			parse_address("Ringstraße 5, 1010 Wien", "AT"),
			{"address_line1": "Ringstraße 5", "pincode": "1010", "city": "Wien"},
		)

	def test_trailing_country_name_is_dropped_from_the_city(self):
		self.assertEqual(parse_address("Musterstr. 12, 48599 Gronau, Deutschland", "DE")["city"], "Gronau")

	def test_house_number_is_not_mistaken_for_the_zip(self):
		# 4-digit number is not a German ZIP, so there's no unmistakable ZIP at all
		self.assertIsNone(parse_address("Industriestr. 1234 Gronau", "DE"))

	def test_unrecognizable_text_is_not_guessed(self):
		self.assertIsNone(parse_address("Gronau", "DE"))
		self.assertIsNone(parse_address("48599 Gronau", "DE"))  # no street
		self.assertIsNone(parse_address("Musterstr. 12, 48599", "DE"))  # no city

	def test_empty_or_unsupported_country_is_not_parsed(self):
		self.assertIsNone(parse_address("", "DE"))
		self.assertIsNone(parse_address(None, "DE"))
		self.assertIsNone(parse_address("1 Main St, 12345 Springfield", "US"))
		self.assertIsNone(parse_address("Musterstr. 12, 48599 Gronau", None))


class TestBuildContact(unittest.TestCase):
	def test_contact_person_of_a_partner_company(self):
		details = {
			"contactPerson": {
				"fullName": "Max Mustermann",
				"email": "max@example.com",
				"phoneNumber": "",
				"companyRole": "IT-Leiter",
			},
			"phone": "+49 123 456",
		}

		self.assertEqual(
			build_contact(details, "Example GmbH"),
			{
				"first_name": "Max",
				"last_name": "Mustermann",
				"designation": "IT-Leiter",
				"email": "max@example.com",
				"phone": "+49 123 456",  # person has no number, falls back to the company phone
			},
		)

	def test_title_stays_with_the_first_name_surname_is_last_word(self):
		contact = build_contact({"contactPerson": {"fullName": "Dr. Erika Musterfrau"}}, "X")

		self.assertEqual((contact["first_name"], contact["last_name"]), ("Dr. Erika", "Musterfrau"))

	def test_single_word_name_is_a_first_name(self):
		contact = build_contact({"contactPerson": {"fullName": "Madonna"}}, "X")

		self.assertEqual((contact["first_name"], contact["last_name"]), ("Madonna", ""))

	def test_company_phone_only_makes_a_contact_named_after_the_company(self):
		contact = build_contact({"phone": " +49 123 456 "}, "Example GmbH")

		self.assertEqual((contact["first_name"], contact["phone"], contact["email"]), ("Example GmbH", "+49 123 456", ""))

	def test_nothing_to_record(self):
		self.assertIsNone(build_contact({"phone": "  ", "contactPerson": {"fullName": "", "email": ""}}, "X"))
		self.assertIsNone(build_contact({}, "X"))
