"""Turn the sparse company details GravityZone returns into data for an
ERPNext Address and Contact.

What GravityZone actually provides (checked on a live cloud tenant): the
address is ONE free-text string, not street/ZIP/city fields, and is often
empty; the company phone is free text; ``contactPerson`` (full name, email,
phone, role) exists only on partner-type companies. So everything here is
best effort and returns None rather than guess.
"""

import re

# Postal-code length per country. Only countries listed here are parsed: with an
# unknown format a wrong guess at street/ZIP/city is worse than no address.
_ZIP_DIGITS = {"DE": 5, "AT": 4}
_TRAILING_COUNTRY = re.compile(r",?\s*(deutschland|germany|österreich|austria|de|at)\.?$", re.IGNORECASE)
_HAS_LETTER = re.compile(r"[^\W\d_]")


def parse_address(text, country_code):
	"""Split ``"Musterstr. 1, 48599 Gronau"`` into ``address_line1``, ``pincode`` and
	``city`` — or return None if the text isn't unmistakably in that shape.
	"""
	digits = _ZIP_DIGITS.get((country_code or "").upper())
	if not digits or not text or not text.strip():
		return None

	flat = re.sub(r"\s*[\n\r;]+\s*", ", ", text.strip())
	match = re.match(rf"^(?P<street>.*\S)[,\s]+(?P<zip>\d{{{digits}}})\s+(?P<city>[^\d\s].*)$", flat)
	if not match:
		return None

	street = match["street"].strip(" ,")
	city = _TRAILING_COUNTRY.sub("", match["city"]).strip(" ,")
	if not (_HAS_LETTER.search(street) and _HAS_LETTER.search(city)):
		return None

	return {"address_line1": street, "pincode": match["zip"], "city": city}


def build_contact(details, company_name):
	"""A Contact from ``contactPerson`` (partner-type companies only) or, failing
	that, just the company phone. None if there is nothing to record.
	"""
	person = details.get("contactPerson") or {}
	full_name = (person.get("fullName") or "").strip()
	email = (person.get("email") or "").strip()
	phone = (person.get("phoneNumber") or "").strip() or (details.get("phone") or "").strip()
	if not (full_name or email or phone):
		return None

	if full_name:
		first_name, _, last_name = full_name.rpartition(" ")
		if not first_name:  # a single word is a first name, not a surname
			first_name, last_name = last_name, ""
	else:
		first_name, last_name = company_name, ""

	return {
		"first_name": first_name,
		"last_name": last_name,
		"designation": (person.get("companyRole") or "").strip(),
		"email": email,
		"phone": phone,
	}
