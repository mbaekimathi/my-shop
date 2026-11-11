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
MSG_CANCELLED = "cancelled"

MSG_STATUS_CHOICES = (
    (MSG_PENDING, "Pending"),
    (MSG_SENT, "Sent"),
    (MSG_FAILED, "Failed"),
    (MSG_MANUAL_REVIEW, "Manual review"),
    (MSG_CANCELLED, "Cancelled"),
)

# Outbound delivery channels. "message" is reserved for the in-app Message API.
CHANNEL_WHATSAPP = "whatsapp"
CHANNEL_SMS = "sms"
CHANNEL_MESSAGE = "message"

CHANNEL_CHOICES = (
    (CHANNEL_WHATSAPP, "WhatsApp"),
    (CHANNEL_SMS, "SMS"),
    (CHANNEL_MESSAGE, "Message"),
)

CHANNEL_VALUES = {value for value, _ in CHANNEL_CHOICES}
CHANNEL_LABELS = dict(CHANNEL_CHOICES)

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

SHARE_PERIOD_CHOICES = (
    ("1", "Today"),
    ("7", "This week"),
    ("14", "Two weeks"),
    ("30", "This month"),
)

SHARE_REPEAT_CHOICES = (
    ("1", "Once"),
    ("2", "2 times"),
    ("3", "3 times"),
    ("4", "4 times"),
    ("7", "Every day in the period"),
)

# Minimum number of shop receipts (transactions) a client must have.
TRANSACTION_MIN_CHOICES = (
    ("", "Any"),
    ("1", "1+"),
    ("2", "2+"),
    ("3", "3+"),
    ("5", "5+"),
    ("10", "10+"),
)

SEND_DELAY_MIN_SECONDS = 1
SEND_DELAY_MAX_SECONDS = 3
SEND_CONCURRENCY = 3
MAX_SEND_ATTEMPTS = 8
SEND_RETRY_BACKOFF_SECONDS = (0.8, 1.6, 3.0, 5.0, 8.0, 12.0, 16.0)
# SMS providers accept many recipients per HTTP call — cap each batched request.
SMS_BATCH_LIMIT = 50

PLACEHOLDERS = (
    "{first_name}",
    "{last_name}",
    "{full_name}",
    "{last_product}",
    "{last_purchase_date}",
    "{lifetime_spend}",
)

CATALOGUE_MESSAGE_PLACEHOLDERS = (
    ("{first_name}", "First name"),
    ("{full_name}", "Full name"),
    ("{items}", "Item list"),
    ("{last_product}", "Last product"),
)

# Triggered message drafts — auto-send one custom message per shop event.
MESSAGE_STATUS_DRAFT = "draft"
MESSAGE_STATUS_ACTIVE = "active"

MESSAGE_STATUS_CHOICES = (
    (MESSAGE_STATUS_DRAFT, "Draft"),
    (MESSAGE_STATUS_ACTIVE, "Active"),
)

# Trigger values match ShopReceiptKind values on purpose; supplier triggers
# are event names, not receipt kinds.
TRIGGER_SALE = "sale"
TRIGGER_CREDIT = "credit"
TRIGGER_CREDIT_FOLLOW_UP = "credit_follow_up"
TRIGGER_CREDIT_FOLLOW_UP_TODAY = "credit_follow_up_today"
TRIGGER_QUOTATION = "quotation"
TRIGGER_TRADE_OUT = "trade_out"
TRIGGER_TRADE_IN = "trade_in"
TRIGGER_SUPPLY = "supply"
TRIGGER_SUPPLIER_PAYMENT = "supplier_payment"
TRIGGER_DELIVERY_ON_WAY = "delivery_on_way"
TRIGGER_DELIVERY_COMPLETE = "delivery_complete"

TRIGGER_MESSAGE_CHOICES = (
    (TRIGGER_SALE, "Sale"),
    (TRIGGER_CREDIT, "Credit sale"),
    (TRIGGER_CREDIT_FOLLOW_UP, "Credit follow-up"),
    (TRIGGER_CREDIT_FOLLOW_UP_TODAY, "Credit follow-up today"),
    (TRIGGER_QUOTATION, "Quotation"),
    (TRIGGER_TRADE_OUT, "Trade out"),
    (TRIGGER_TRADE_IN, "Trade in"),
    (TRIGGER_SUPPLY, "Supply received"),
    (TRIGGER_SUPPLIER_PAYMENT, "Supplier paid"),
    (TRIGGER_DELIVERY_ON_WAY, "Delivery on the way"),
    (TRIGGER_DELIVERY_COMPLETE, "Delivery completed"),
)

# Grouped for the message-drafts composer select.
TRIGGER_MESSAGE_GROUPS = (
    (
        "Sales & documents",
        (
            (TRIGGER_SALE, "Sale"),
            (TRIGGER_CREDIT, "Credit sale"),
            (TRIGGER_CREDIT_FOLLOW_UP, "Credit follow-up"),
            (TRIGGER_CREDIT_FOLLOW_UP_TODAY, "Credit follow-up today"),
            (TRIGGER_QUOTATION, "Quotation"),
            (TRIGGER_TRADE_OUT, "Trade out"),
            (TRIGGER_TRADE_IN, "Trade in"),
        ),
    ),
    (
        "Delivery",
        (
            (TRIGGER_DELIVERY_ON_WAY, "Delivery on the way"),
            (TRIGGER_DELIVERY_COMPLETE, "Delivery completed"),
        ),
    ),
    (
        "Suppliers",
        (
            (TRIGGER_SUPPLY, "Supply received"),
            (TRIGGER_SUPPLIER_PAYMENT, "Supplier paid"),
        ),
    ),
)

DEFAULT_DELIVERY_DRAFTS = (
    (
        TRIGGER_DELIVERY_ON_WAY,
        (
            "Hi {first_name}, good news — your delivery from {shop_name} is on "
            "the way! Drop-off: {location}. Rider: {rider_name}. "
            "Receipt {receipt_number}."
        ),
        "",
    ),
    (
        TRIGGER_DELIVERY_COMPLETE,
        (
            "Hi {first_name}, your delivery from {shop_name} is complete! "
            "Drop-off: {location}. Received by: {receiver_name}. "
            "Receipt {receipt_number}. Thank you!"
        ),
        "",
    ),
)

# (trigger, body, link) — company defaults seeded on the message-drafts page.
DEFAULT_CREDIT_DRAFTS = (
    (
        TRIGGER_CREDIT,
        (
            "Hi {first_name},\n"
            "\n"
            "Credit sale at {shop_name}.\n"
            "\n"
            "Amount due: {balance}\n"
            "Pay by: {due_date}\n"
            "\n"
            "Receipt {receipt_number}.\n"
            "\n"
            "You can view your credit account and pay with M-Pesa using this link:"
        ),
        "{account_link}",
    ),
    (
        TRIGGER_CREDIT_FOLLOW_UP,
        (
            "Hi {first_name},\n"
            "\n"
            "A payment follow-up was scheduled for your credit account.\n"
            "\n"
            "Amount due: {balance}\n"
            "Follow-up date: {follow_up_date}\n"
            "\n"
            "You can view your credit account and pay with M-Pesa using this link:"
        ),
        "{account_link}",
    ),
    (
        TRIGGER_CREDIT_FOLLOW_UP_TODAY,
        (
            "Hi {first_name},\n"
            "\n"
            "Friendly reminder — we are following up on your credit account today.\n"
            "\n"
            "Amount due: {balance}\n"
            "Follow-up date: {follow_up_date}\n"
            "\n"
            "You can view your credit account and pay with M-Pesa using this link:"
        ),
        "{account_link}",
    ),
)

TRIGGER_MESSAGE_PLACEHOLDERS = (
    ("{first_name}", "Customer first name"),
    ("{full_name}", "Customer full name"),
    ("{shop_name}", "Shop name"),
    ("{total}", "Receipt total"),
    ("{balance}", "Balance due"),
    ("{receipt_number}", "Receipt number"),
    ("{due_date}", "Credit due date"),
    ("{follow_up_date}", "Credit follow-up date"),
    ("{previous_follow_up}", "Previous follow-up date"),
    ("{account_link}", "Credit account link"),
    ("{trade_link}", "Trade account link"),
    ("{receipt_link}", "Receipt link"),
    ("{quotation_link}", "Quotation link"),
    ("{location}", "Delivery location"),
    ("{rider_name}", "Rider name"),
    ("{receiver_name}", "Who received the goods"),
)

# Delivery channel for a triggered message draft.
TRIGGER_CHANNEL_WHATSAPP = CHANNEL_WHATSAPP
TRIGGER_CHANNEL_SMS = CHANNEL_SMS
TRIGGER_CHANNEL_MESSAGE = CHANNEL_MESSAGE
TRIGGER_CHANNEL_BOTH = "both"
TRIGGER_CHANNEL_ALL = "all"

TRIGGER_CHANNEL_CHOICES = (
    (TRIGGER_CHANNEL_WHATSAPP, "WhatsApp"),
    (TRIGGER_CHANNEL_SMS, "SMS"),
    (TRIGGER_CHANNEL_MESSAGE, "Email / Message"),
    (TRIGGER_CHANNEL_BOTH, "WhatsApp + SMS"),
    (TRIGGER_CHANNEL_ALL, "WhatsApp + SMS + Email"),
)

TRIGGER_CHANNEL_VALUES = {value for value, _ in TRIGGER_CHANNEL_CHOICES}

# Prefix used for the campaign ``trigger_label`` stored on auto-sent messages.
TRIGGER_LABEL_PREFIX = "Auto message · "

# "Send after N <unit>" — stored as minutes on the draft.
TRIGGER_DELAY_UNITS = (
    ("minutes", "minutes", 1),
    ("hours", "hours", 60),
    ("days", "days", 1440),
)

TRIGGER_DELAY_MAX_MINUTES = 43200  # 30 days

# Who should get this? — primary audience groups
AUDIENCE_SALE = "sale"
AUDIENCE_CREDIT = "credit"
AUDIENCE_QUOTATION = "quotation"
AUDIENCE_LEADS = "leads"
AUDIENCE_WHATSAPP = "whatsapp"

AUDIENCE_TYPE_CHOICES = (
    (AUDIENCE_SALE, "Sales"),
    (AUDIENCE_CREDIT, "Credits"),
    (AUDIENCE_QUOTATION, "Quotations"),
    (AUDIENCE_LEADS, "Leads"),
)

AUDIENCE_TYPES = {value for value, _ in AUDIENCE_TYPE_CHOICES}
