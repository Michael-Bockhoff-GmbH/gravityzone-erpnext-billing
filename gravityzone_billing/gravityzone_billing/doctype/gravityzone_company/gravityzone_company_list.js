// Copyright (c) 2026, Michael Bockhoff GmbH and contributors
// For license information, please see license.txt

function show_customer_result(r, listview) {
	const { created = 0, linked = 0, skipped = 0, addresses = 0, contacts = 0, notes = [] } = r.message || {};
	const nothing_to_do = !created && !linked;
	let message = nothing_to_do
		? __("Nothing to do: every GravityZone Company already has an ERPNext Customer or is excluded from sync.")
		: __("Created {0}, linked {1} existing Customer(s) with the same name, skipped {2} that already had one.", [
				created,
				linked,
				skipped,
			]);
	if (created) {
		message += " " + __("Imported {0} address(es) and {1} contact(s) from GravityZone.", [addresses, contacts]);
	}
	if (notes.length) {
		message += "<br><br>" + notes.map((n) => frappe.utils.escape_html(n)).join("<br>");
	}
	frappe.msgprint({
		title: __("ERPNext Customers"),
		message,
		indicator: nothing_to_do ? "orange" : "green",
	});
	listview.refresh();
}

frappe.listview_settings["GravityZone Company"] = {
	onload(listview) {
		// Always visible, no selection needed: every company that has no Customer yet
		// (companies excluded from sync are left out).
		listview.page.add_inner_button(__("Create ERPNext Customers"), () => {
			frappe.confirm(
				__(
					"Create an ERPNext Customer for every GravityZone Company that doesn't have one yet? Companies excluded from sync are skipped."
				),
				() => {
					frappe.call({
						method: "gravityzone_billing.sync.create_missing_erpnext_customers",
						freeze: true,
						freeze_message: __("Creating Customers..."),
					}).then((r) => show_customer_result(r, listview));
				}
			);
		});

		// Same thing for just the rows you ticked (shows up in the Actions menu).
		listview.page.add_actions_menu_item(__("Create ERPNext Customers"), () => {
			const names = listview.get_checked_items(true);
			if (!names.length) {
				frappe.msgprint(__("Select at least one GravityZone Company first."));
				return;
			}

			frappe.call({
				method: "gravityzone_billing.sync.create_erpnext_customers",
				args: { company_names: names },
				freeze: true,
				freeze_message: __("Creating Customers..."),
			}).then((r) => show_customer_result(r, listview));
		});
	},
};
