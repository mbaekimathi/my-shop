"""Constants for personal WhatsApp broadcast segmentation and sends."""

from decimal import Decimal

BRIDGE_STATUS_DISCONNECTED = "disconnected"
BRIDGE_STATUS_QR_PENDING = "qr_pending"
BRIDGE_STATUS_CONNECTED = "connected"

BRIDGE_STATUS_CHOICES = (
    (BRIDGE_STATUS_DISCONNECTED, "Disconnected"),
    (BRIDGE_STATUS_QR_PENDING, "QR pending"),
    (BRIDGE_STATUS_CONNECTED, "Connected"),
)

CAMPAIGN_DRAFT = "draft"
CAMPAIGN_QUEUED = "queued"
CAMPAIGN_SENDING = "sending"
CAMPAIGN_DONE = "done"
CAMPAIGN_CANCELLED = "cancelled"

CAMPAIGN_STATUS_CHOICES = (
    (CAMPAIGN_DRAFT, "Draft"),
    (CAMPAIGN_QUEUED, "Queued"),
    (CAMPAIGN_SENDING, "Sending"),
    (CAMPAIGN_DONE, "Done"),
    (CAMPAIGN_CANCELLED, "Cancelled"),
)

MSG_PENDING = "pending"
MSG_SENT = "sent"
MSG_FAILED = "failed"
MSG_MANUAL_REVIEW = "manual_review"

MSG_STATUS_CHOICES = (
    (MSG_PENDING, "Pending"),
    (MSG_SENT, "Sent"),
    (MSG_FAILED, "Failed"),
    (MSG_MANUAL_REVIEW, "Manual review"),
)

# Lifetime spend tiers (KES) based on sum of sale/credit receipt totals.
SPEND_TIER_LOW = "low"
SPEND_TIER_MID = "mid"
SPEND_TIER_HIGH = "high"

SPEND_TIER_CHOICES = (
    (SPEND_TIER_LOW, "Low spenders"),
    (SPEND_TIER_MID, "Medium spenders"),
    (SPEND_TIER_HIGH, "High spenders"),
)

SPEND_TIER_BOUNDS = {
    SPEND_TIER_LOW: (Decimal("0"), Decimal("9999.99")),
    SPEND_TIER_MID: (Decimal("10000"), Decimal("49999.99")),
    SPEND_TIER_HIGH: (Decimal("50000"), None),
}

LAST_PURCHASE_WINDOWS = {
    "": "Any time",
    "7": "This week",
    "30": "This month",
    "90": "Last 3 months",
    "180": "Last 6 months",
    "365": "This year",
}

# Minimum number of shop receipts (transactions) a client must have.
TRANSACTION_MIN_CHOICES = (
    ("", "Any"),
    ("1", "1+"),
    ("2", "2+"),
    ("3", "3+"),
    ("5", "5+"),
    ("10", "10+"),
)

SEND_DELAY_MIN_SECONDS = 5
SEND_DELAY_MAX_SECONDS = 20
MAX_SEND_ATTEMPTS = 2  # initial + one retry

PLACEHOLDERS = (
    "{first_name}",
    "{last_name}",
    "{full_name}",
    "{last_product}",
    "{last_purchase_date}",
    "{lifetime_spend}",
)

# Who should get this? — primary audience groups
AUDIENCE_SALE = "sale"
AUDIENCE_CREDIT = "credit"
AUDIENCE_QUOTATION = "quotation"
AUDIENCE_LEADS = "leads"
AUDIENCE_WHATSAPP = "whatsapp"

AUDIENCE_TYPE_CHOICES = (
    (AUDIENCE_WHATSAPP, "WhatsApp"),
    (AUDIENCE_SALE, "Sales"),
    (AUDIENCE_CREDIT, "Credits"),
    (AUDIENCE_QUOTATION, "Quotations"),
    (AUDIENCE_LEADS, "Leads"),
)

AUDIENCE_TYPES = {value for value, _ in AUDIENCE_TYPE_CHOICES}
