SIGNAL_TAXONOMY = {
    "Fulfilment": [
        "Selenium auto-FF failure",
        "Vendor API timeout",
        "Manual inventory mismatch",
        "Low inventory at booking",
        "Slot sold out",
        "Reschedule delay",
        "Tickets not delivered",
        "Late delivery (T-24h missed)",
    ],
    "Process & SLA": [
        "TAT breach",
        "SLA breach (response)",
        "Slack alert missed",
        "No retry mechanism",
        "Manual intervention required",
        "Repeat issue same VID",
    ],
    "AI / Automation": [
        "AI mishandle",
        "AI escalation gap",
        "Playbook missing branch",
    ],
    "SP / Vendor": [
        "SP system outage",
        "SP cancelled at venue",
        "SP overbooking",
        "Poor SP completion rate",
        "Multiple complaints same VID",
    ],
    "Customer Impact": [
        "Chargeback initiated",
        "On-site repurchase",
        "Refund delay",
        "Public escalation",
        "Frustration / threat to escalate",
        "Negative on-site experience",
    ],
    "Product / UX": [
        "Misleading listing info",
        "Inventory not visible at booking",
        "Confusing checkout",
    ],
}

ALL_SIGNALS = [s for cat in SIGNAL_TAXONOMY.values() for s in cat]
