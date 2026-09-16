// Copyright (c) 2026, Michael Bockhoff GmbH and contributors
// For license information, please see license.txt

frappe.ui.form.on("GravityZone Company", {
	refresh(frm) {
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
