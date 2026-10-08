// Copyright (c) 2026, Michael Bockhoff GmbH and contributors
// For license information, please see license.txt

function customer_result_message(result) {
	const { created = 0, linked = 0, skipped = 0, addresses = 0, contacts = 0, notes = [] } = result || {};
	let message = __("Created {0}, linked {1} existing Customer(s), skipped {2}.", [created, linked, skipped]);
	if (created) {
		message += " " + __("Imported {0} address(es) and {1} contact(s) from GravityZone.", [addresses, contacts]);
	}
	if (notes.length) {
		message += "<br><br>" + notes.map((n) => frappe.utils.escape_html(n)).join("<br>");
	}
	return message;
}

// A function, so the names are translated when used, not when this file loads.
function subscription_type_name(code) {
	const names = {
		1: __("Trial"),
		2: __("Licensed"),
		3: __("Monthly"),
		4: __("Monthly license trial"),
		5: __("Monthly subscription trial"),
		6: "FRAT",
	};
	return names[code] || code;
}

function license_list(licenses, empty_text) {
	const esc = frappe.utils.escape_html;
	const entries = Object.entries(licenses || {});
	if (!entries.length) {
		return `<div class="text-muted">${empty_text}</div>`;
	}
	return "<ul>" + entries.map(([name, qty]) => `<li>${esc(name)}: <b>${qty}</b></li>`).join("") + "</ul>";
}

// Rendered here from the stored data (not a stored sentence), so it follows the user's language.
function render_licenses(frm) {
	const field = frm.fields_dict.licenses_summary;
	if (!field) {
		return;
	}
	let data = null;
	try {
		data = JSON.parse(frm.doc.licenses_data || "");
	} catch (e) {
		data = null;
	}
	if (!data) {
		field.$wrapper.html(
			`<div class="text-muted">${__("Not read yet. Run Discover License Types in GravityZone Settings.")}</div>`
		);
		return;
	}

	const esc = frappe.utils.escape_html;
	const row = (label, value) => `<div>${label}: <b>${esc(String(value))}</b></div>`;
	let html = row(__("Model"), data.model || "n/a");
	const subscription = subscription_type_name(data.subscription);
	html += row(__("Subscription"), subscription == null ? "n/a" : subscription);
	if ((data.additional || []).length) {
		html += row(__("Additional products"), data.additional.join(", "));
	}

	if (data.sub_companies) {
		html += `<div class="mt-3"><b>${__("Own licenses")}</b></div>` + license_list(data.licenses, __("none"));
		html += `<div class="mt-3"><b>${__("Sub-companies ({0})", [data.sub_companies.length])}</b></div>`;
		html += data.sub_companies
			.map((sub) => `<div class="mt-2">${esc(sub.name)}</div>` + license_list(sub.licenses, __("no licenses")))
			.join("");
	} else {
		html += `<div class="mt-3"><b>${__("Licenses")}</b></div>` + license_list(data.licenses, __("none"));
	}
	field.$wrapper.html(html);
}

frappe.ui.form.on("GravityZone Company", {
	refresh(frm) {
		render_licenses(frm);
		if (!frm.is_new()) {
			// Always available. With a Customer already linked it asks first, so a wrong click
			// on a correctly linked company can't silently re-link it.
			const customer = frm.doc.customer;
			frm.add_custom_button(__("Create ERPNext Customer"), () => {
				const create = () =>
					frappe.call({
						method: "gravityzone_billing.sync.create_erpnext_customers",
						args: { company_names: [frm.doc.name], replace_existing: customer ? 1 : 0 },
						freeze: true,
						freeze_message: __("Creating Customer..."),
					}).then((r) => {
						frappe.msgprint(customer_result_message(r.message));
						frm.reload_doc();
					});

				if (!customer) {
					return create();
				}

				const esc = frappe.utils.escape_html;
				let warning = __(
					"{0} is already linked to the ERPNext Customer {1}. Link it to a Customer named after the GravityZone company instead? An existing Customer with exactly that name is reused, otherwise a new one is created. The old Customer is not changed or deleted.",
					[esc(frm.doc.gz_company_name || frm.doc.name), esc(customer)]
				);
				if (frm.doc.subscription) {
					warning +=
						"<br><br>" +
						__(
							"The billing document {0} belongs to the old Customer: it stays there and is no longer updated. The next sync creates a new one for the new Customer, and you'd cancel the old one yourself.",
							[esc(frm.doc.subscription)]
						);
				}
				frappe.confirm(warning, create);
			}).addClass(customer ? "btn-default" : "btn-primary");
		}

		if (!frm.doc.needs_review) {
			return;
		}

		const flagged_lines = (frm.doc.product_lines || []).filter((row) => row.needs_review);
		const headline = flagged_lines.length
			? __("{0} product(s) exceed the review threshold and were not applied: {1}. Review and approve below.", [
					flagged_lines.length,
					flagged_lines.map((row) => `${row.label} (${row.pending_qty})`).join(", "),
				])
			: __("Synced license count of {0} exceeds the review threshold and was not applied. Review and approve below.", [
					frm.doc.pending_qty,
				]);

		frm.dashboard.set_headline_alert(headline, "orange");

		frm.add_custom_button(__("Approve Pending Change(s)"), () => {
			frappe.confirm(__("Apply all pending changes shown above to this customer's Subscription?"), () => {
				frappe.call({
					method: "gravityzone_billing.sync.approve_all_pending",
					args: { company_name: frm.doc.name },
					freeze: true,
					freeze_message: __("Applying..."),
				}).then(() => frm.reload_doc());
			});
		}).addClass("btn-warning");
	},
});
