// Copyright (c) 2026, Michael Bockhoff GmbH and contributors
// For license information, please see license.txt

frappe.ui.form.on("GravityZone Company", {
	refresh(frm) {
		if (frm.doc.needs_review) {
			frm.dashboard.set_headline_alert(
				__("Synced license count of {0} exceeds the review threshold and was not applied. Review and approve below.", [
					frm.doc.pending_qty,
				]),
				"orange"
			);
			frm.add_custom_button(__("Approve Pending Change"), () => {
				frappe.confirm(
					__("Apply the pending license count of {0} to this customer's Subscription?", [frm.doc.pending_qty]),
					() => {
						frappe.call({
							method: "gravityzone_billing.sync.approve_pending_change",
							args: { company_name: frm.doc.name },
							freeze: true,
							freeze_message: __("Applying..."),
						}).then(() => frm.reload_doc());
					}
				);
			}).addClass("btn-warning");
		}
	},
});
