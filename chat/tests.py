"""Chat tests.

Standalone MY-CHATBOT historically used SQLite and created unmanaged MY-SHOP
tables via schema_editor. Inside MY-SHOP, prefer MySQL (full suite):

    python manage.py test chat -v 1

When `shops`/`items` are installed, those tables come from real migrations
and are not created/dropped by this module.
"""

import json
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.hashers import make_password
from django.db import connection
from django.test import Client, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from . import services
from .bot import reply_for
from .models import Conversation, ConversationKind, Message
from . import myshop_models as mm


def _myshop_apps_installed():
    """When chat runs inside MY-SHOP, real apps already own these tables."""
    from django.apps import apps

    return apps.is_installed("shops") and apps.is_installed("items")


# Prefer real MY-SHOP models for fixtures when embedded (full column defaults /
# FKs). Chat runtime still reads via chat.myshop_models unmanaged mirrors.
if _myshop_apps_installed():
    from items.models import Item, ShopStock, StoreStock
    from shops.models import (
        CompanyAnalyticsSettings,
        CompanyPosSettings,
        CompanyProfile,
        Shop,
        ShopDelivery,
        ShopPosSettings,
        ShopReceipt,
        ShopReceiptLine,
    )

    ShopDelivery.STATUS_OPEN = mm.ShopDelivery.STATUS_OPEN
    ShopDelivery.STATUS_ACCEPTED = mm.ShopDelivery.STATUS_ACCEPTED
    ShopDelivery.STATUS_DELIVERED = mm.ShopDelivery.STATUS_DELIVERED
    ShopDelivery.STATUS_DELIVERED_PAID = mm.ShopDelivery.STATUS_DELIVERED_PAID
    ShopDelivery.STATUS_COMPLETED = mm.ShopDelivery.STATUS_COMPLETED
    ShopDelivery.STATUS_CANCELLED = mm.ShopDelivery.STATUS_CANCELLED
    ShopDelivery.DONE_STATUSES = mm.ShopDelivery.DONE_STATUSES

    ShopDaySession = mm.ShopDaySession
    Expense = mm.Expense
    ExpenseSupplier = mm.ExpenseSupplier
    ShopItemPrice = mm.ShopItemPrice
    ItemSerial = mm.ItemSerial
    Supplier = mm.Supplier
    StockMovement = mm.StockMovement
    StockMovementLine = mm.StockMovementLine
    StoreStockMovement = mm.StoreStockMovement
    ShopDeliveryAssignedRider = mm.ShopDeliveryAssignedRider
    ClientModel = mm.Client
else:
    Shop = mm.Shop
    Item = mm.Item
    ShopStock = mm.ShopStock
    ShopReceipt = mm.ShopReceipt
    ShopReceiptLine = mm.ShopReceiptLine
    ShopDaySession = mm.ShopDaySession
    Expense = mm.Expense
    ExpenseSupplier = mm.ExpenseSupplier
    ShopItemPrice = mm.ShopItemPrice
    CompanyProfile = mm.CompanyProfile
    CompanyAnalyticsSettings = mm.CompanyAnalyticsSettings
    CompanyPosSettings = mm.CompanyPosSettings
    ShopPosSettings = mm.ShopPosSettings
    ItemSerial = mm.ItemSerial
    Supplier = mm.Supplier
    StockMovement = mm.StockMovement
    StockMovementLine = mm.StockMovementLine
    StoreStock = mm.StoreStock
    StoreStockMovement = mm.StoreStockMovement
    ShopDelivery = mm.ShopDelivery
    ShopDeliveryAssignedRider = mm.ShopDeliveryAssignedRider
    ClientModel = mm.Client

UNMANAGED = [
    Shop,
    Item,
    ShopStock,
    ShopReceipt,
    ShopReceiptLine,
    ShopDaySession,
    Expense,
    ExpenseSupplier,
    ShopItemPrice,
    CompanyProfile,
    CompanyAnalyticsSettings,
    CompanyPosSettings,
    ShopPosSettings,
    ItemSerial,
    Supplier,
    StockMovement,
    StockMovementLine,
    StoreStock,
    StoreStockMovement,
    ShopDelivery,
    ShopDeliveryAssignedRider,
    ClientModel,
]


def create_unmanaged_tables():
    if _myshop_apps_installed():
        return
    with connection.schema_editor() as editor:
        for model in UNMANAGED:
            editor.create_model(model)


def drop_unmanaged_tables():
    if _myshop_apps_installed():
        return
    with connection.schema_editor() as editor:
        for model in reversed(UNMANAGED):
            try:
                editor.delete_model(model)
            except Exception:
                pass


def _clear_fixture_tables():
    """Wipe chat fixture tables between TransactionTestCase tests."""
    if _myshop_apps_installed():
        with connection.cursor() as cursor:
            cursor.execute("SET FOREIGN_KEY_CHECKS=0")
            try:
                for model in UNMANAGED:
                    model.objects.all().delete()
            finally:
                cursor.execute("SET FOREIGN_KEY_CHECKS=1")
        return
    for model in UNMANAGED:
        model.objects.all().delete()


def ensure_employee_scratch_tables(ddl_statements):
    """Create minimal employee tables for standalone SQLite chatbot tests."""
    if _myshop_apps_installed():
        return
    with connection.cursor() as cur:
        for ddl in ddl_statements:
            cur.execute(ddl)


def reset_employee_rows():
    """Clear employee profile rows used by chat auth fixtures."""
    with connection.cursor() as cur:
        cur.execute("DELETE FROM employees_employeeprofile_assigned_shops")
        cur.execute("DELETE FROM employees_employeeprofile")


def insert_employee_profile(*, pk, employee_id, user_id, role, status="active"):
    """Insert an employee row (ORM when MY-SHOP employees app is installed)."""
    # Chatbot fixtures historically used free-form roles; map onto MY-SHOP enums.
    role_aliases = {
        "shop_attendant": "employee",
    }
    role = role_aliases.get(role, role)
    if _myshop_apps_installed():
        from django.contrib.auth.models import User
        from employees.models import EmployeeProfile

        if user_id is None:
            user = User.objects.create_user(
                username=f"chat-emp-{pk}-{employee_id}",
                password="x",
            )
            user_id = user.pk
        else:
            user = User.objects.get(pk=user_id)
        EmployeeProfile.objects.filter(pk=pk).delete()
        EmployeeProfile.objects.filter(employee_id=str(employee_id)).delete()
        # OneToOne user — clear any prior profile for this user.
        EmployeeProfile.objects.filter(user_id=user_id).delete()
        profile = EmployeeProfile(
            id=pk,
            user=user,
            employee_id=str(employee_id),
            phone_number="0700000000",
            role=role,
            status=status,
        )
        profile.save()
        return profile
    with connection.cursor() as cur:
        cur.execute(
            "DELETE FROM employees_employeeprofile_assigned_shops "
            "WHERE employeeprofile_id=%s",
            [pk],
        )
        cur.execute(
            "DELETE FROM employees_employeeprofile WHERE id=%s OR employee_id=%s",
            [pk, employee_id],
        )
        if user_id is not None:
            cur.execute(
                "DELETE FROM employees_employeeprofile WHERE user_id=%s",
                [user_id],
            )
        cur.execute(
            "INSERT INTO employees_employeeprofile "
            "(id, employee_id, user_id, role, status) "
            "VALUES (%s, %s, %s, %s, %s)",
            [pk, employee_id, user_id, role, status],
        )
    return None


def assign_employee_shop(*, employee_pk, shop_id):
    if _myshop_apps_installed():
        from employees.models import EmployeeProfile

        EmployeeProfile.objects.get(pk=employee_pk).assigned_shops.add(shop_id)
        return
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO employees_employeeprofile_assigned_shops "
            "(employeeprofile_id, shop_id) VALUES (%s, %s)",
            [employee_pk, shop_id],
        )


class ChatTestCase(TransactionTestCase):
    # TransactionTestCase: no wrapping atomic(), so schema_editor can create
    # the unmanaged MY-SHOP tables in setUpClass below.
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        create_unmanaged_tables()

    @classmethod
    def tearDownClass(cls):
        drop_unmanaged_tables()
        super().tearDownClass()

    def setUp(self):
        # TransactionTestCase does not flush unmanaged tables between tests —
        # clear them so each test sees a clean MY-SHOP schema.
        _clear_fixture_tables()
        self.shop_a = Shop.objects.create(
            name="Alpha Store", location="Nairobi CBD", email="a@x.co",
            phone_number="0700000001", login_code="111111",
            password_hash=make_password("pass-a"), is_suspended=False,
            is_hidden=False,
        )
        self.shop_b = Shop.objects.create(
            name="Beta Mart", location="Westlands", email="b@x.co",
            phone_number="0700000002", login_code="222222",
            password_hash=make_password("pass-b"), is_suspended=False,
            is_hidden=False,
        )
        # Real MY-SHOP receipts require created_by_id → employees_employeeprofile.
        from django.contrib.auth.models import User

        self.staff_user = User.objects.create_user(
            username="chat-staff", password="pass-staff"
        )
        self.staff_pk = 1
        self.staff_employee_id = "900001"
        insert_employee_profile(
            pk=self.staff_pk,
            employee_id=self.staff_employee_id,
            user_id=self.staff_user.pk,
            role="shop_cashier",
            status="active",
        )
        self.client = Client()

    def create_receipt(self, **kwargs):
        kwargs.setdefault("created_by_id", self.staff_pk)
        return ShopReceipt.objects.create(**kwargs)

    def reseed_staff(self):
        insert_employee_profile(
            pk=self.staff_pk,
            employee_id=self.staff_employee_id,
            user_id=self.staff_user.pk,
            role="shop_cashier",
            status="active",
        )

    def prepare_employee_fixtures(self):
        ensure_employee_scratch_tables(getattr(self, "EMPLOYEE_DDL", ()))
        reset_employee_rows()
        self.reseed_staff()

    def login(self, code="111111", password="pass-a"):
        return self.client.post(
            reverse("chat:login"),
            {"login_code": code, "password": password},
        )


class IconTests(ChatTestCase):
    def test_icon_falls_back_to_my_shop(self):
        from . import icons

        self.assertEqual(icons.icon_label(), "MY SHOP")
        png = icons.render_app_icon(192, rounded=True)
        self.assertTrue(png.startswith(b"\x89PNG"))

    def test_icon_uses_company_name(self):
        from . import icons

        CompanyProfile.objects.create(
            name="Acme Retail",
            phone_number="0700",
            email="a@x.co",
            location="Nairobi",
        )
        self.assertEqual(icons.icon_label(), "Acme Retail")

    def test_app_icon_endpoint_and_manifest(self):
        resp = self.client.get(reverse("chat:app_icon", kwargs={"size": 192}))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "image/png")
        self.assertTrue(resp.content.startswith(b"\x89PNG"))

        manifest = self.client.get(reverse("chat:manifest")).json()
        self.assertEqual(manifest["id"], reverse("chat:home"))
        self.assertEqual(manifest["scope"], reverse("chat:home"))
        self.assertEqual(manifest["start_url"], reverse("chat:app"))
        self.assertTrue(manifest["icons"])
        icon_src = manifest["icons"][0]["src"]
        self.assertTrue(icon_src.startswith(reverse("chat:app_icon", kwargs={"size": 192})))
        self.assertIn("v=", icon_src)

        sw = self.client.get(reverse("chat:service_worker"))
        self.assertEqual(sw.status_code, 200)
        self.assertIn("javascript", sw["Content-Type"])
        self.assertIn("no-cache", sw["Cache-Control"])
        self.assertEqual(sw["Service-Worker-Allowed"], reverse("chat:home"))


class AuthTests(ChatTestCase):
    def test_login_success(self):
        resp = self.login()
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse("chat:app"), resp["Location"])

    def test_login_wrong_password(self):
        resp = self.login(password="wrong")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Invalid shop code or password")

    def test_suspended_shop_rejected(self):
        self.shop_a.is_suspended = True
        self.shop_a.save()
        resp = self.login()
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Invalid shop code or password")

    def test_app_requires_login(self):
        resp = self.client.get(reverse("chat:app"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse("chat:login"), resp["Location"])


class EmployeeRoleLoginTests(ChatTestCase):
    """Any active employee may sign in; admins/IT get /all/, others get
    a WhatsApp-style role dashboard."""

    EMPLOYEE_DDL = (
        "CREATE TABLE IF NOT EXISTS employees_employeeprofile ("
        "id INTEGER PRIMARY KEY, employee_id VARCHAR(12) NOT NULL, "
        "user_id BIGINT, role VARCHAR(32) NOT NULL, "
        "status VARCHAR(16) NOT NULL)",
        "CREATE TABLE IF NOT EXISTS "
        "employees_employeeprofile_assigned_shops ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "employeeprofile_id BIGINT NOT NULL, shop_id BIGINT NOT NULL)",
    )

    def setUp(self):
        super().setUp()
        from django.contrib.auth.models import User

        self.prepare_employee_fixtures()
        self.cashier_user = User.objects.create_user(
            username="cashier1", password="pass-c"
        )
        self.it_user = User.objects.create_user(
            username="itsupport", password="pass-i"
        )
        self.admin_user = User.objects.create_user(
            username="superadmin", password="pass-a"
        )
        insert_employee_profile(
            pk=10, employee_id="200001", user_id=self.cashier_user.pk,
            role="shop_cashier", status="active",
        )
        insert_employee_profile(
            pk=11, employee_id="200002", user_id=self.it_user.pk,
            role="it_support", status="active",
        )
        insert_employee_profile(
            pk=12, employee_id="200003", user_id=self.admin_user.pk,
            role="super_admin", status="active",
        )

    def employee_login(self, code, password):
        return self.client.post(
            reverse("chat:login"),
            {"mode": "employee", "login_code": code, "password": password},
        )

    def test_cashier_lands_on_role_page(self):
        resp = self.employee_login("200001", "pass-c")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], reverse("chat:role_home", args=["shop-cashier"]))
        page = self.client.get(resp["Location"])
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Shop Cashier")
        self.assertContains(page, "Dashboard")
        self.assertContains(page, "No shops are allocated")

    def test_it_support_lands_on_all_shops(self):
        resp = self.employee_login("200002", "pass-i")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], reverse("chat:all_shops"))

    def test_super_admin_lands_on_all_shops(self):
        resp = self.employee_login("200003", "pass-a")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], reverse("chat:all_shops"))

    def test_cashier_blocked_from_all_shops(self):
        self.employee_login("200001", "pass-c")
        resp = self.client.get(reverse("chat:all_shops"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            resp["Location"], reverse("chat:role_home", args=["shop-cashier"])
        )

    def test_wrong_employee_password_rejected(self):
        resp = self.employee_login("200001", "wrong")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Invalid staff ID or password")

    def test_role_slug_mismatch_redirects_home(self):
        self.employee_login("200001", "pass-c")
        resp = self.client.get(reverse("chat:role_home", args=["rider"]))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            resp["Location"], reverse("chat:role_home", args=["shop-cashier"])
        )


class DirectChatTests(ChatTestCase):
    def setUp(self):
        super().setUp()
        self.login()

    def test_open_direct_conversation(self):
        resp = self.client.post(
            reverse("chat:api_open"),
            data='{"target": "%s"}' % self.shop_b.pk,
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["conversation"]["kind"], "direct")
        self.assertEqual(data["conversation"]["peer_shop_id"], self.shop_b.pk)

    def test_send_and_poll_direct(self):
        convo = services.get_or_create_direct(self.shop_a, self.shop_b)
        resp = self.client.post(
            reverse("chat:api_send", args=[convo.pk]),
            data='{"body": "Hello Beta", "client_tag": "t1"}',
            content_type="application/json",
        )
        self.assertTrue(resp.json()["ok"])
        self.assertEqual(
            Message.objects.filter(conversation=convo).count(), 1
        )
        # Duplicate client_tag must not repost.
        resp = self.client.post(
            reverse("chat:api_send", args=[convo.pk]),
            data='{"body": "Hello Beta", "client_tag": "t1"}',
            content_type="application/json",
        )
        self.assertEqual(
            Message.objects.filter(conversation=convo).count(), 1
        )
        # Shop B sees the message and an unread badge.
        client_b = Client()
        client_b.post(
            reverse("chat:login"),
            {"login_code": "222222", "password": "pass-b"},
        )
        resp = client_b.get(reverse("chat:api_poll"))
        convos = {c["id"]: c for c in resp.json()["conversations"]}
        self.assertEqual(convos[convo.pk]["unread"], 1)
        resp = client_b.get(
            reverse("chat:api_poll"),
            {"conversation_id": convo.pk},
        )
        active = resp.json()["active"]
        self.assertEqual(len(active["messages"]), 1)
        self.assertEqual(active["messages"][0]["body"], "Hello Beta")
        self.assertFalse(active["messages"][0]["mine"])

    def test_cannot_send_to_foreign_conversation(self):
        shop_c = Shop.objects.create(
            name="Gamma", location="Kisumu", email="c@x.co",
            phone_number="0700000003", login_code="333333",
            password_hash=make_password("pass-c"), is_suspended=False,
            is_hidden=False,
        )
        convo = services.get_or_create_direct(self.shop_b, shop_c)
        resp = self.client.post(
            reverse("chat:api_send", args=[convo.pk]),
            data='{"body": "intruder"}',
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 404)


class BotTests(ChatTestCase):
    def setUp(self):
        super().setUp()
        self.login()
        # Match confirmed receipt fixtures unless a test overrides the setting.
        CompanyAnalyticsSettings.objects.create(
            pk=1, receipt_data_source="confirmed"
        )
        item = Item.objects.create(
            category="Grocery", name="Sugar 1kg",
            minimum_selling_price=Decimal("100"),
            shop_price=Decimal("150"), stock=Decimal("40"),
            low_stock_notify=False, low_stock_threshold=0,
            is_suspended=False,
        )
        ShopStock.objects.create(
            shop_id=self.shop_a.pk, item_id=item.pk,
            quantity=Decimal("3"), low_stock_threshold=5,
            average_cost=Decimal("80"),
        )
        self.receipt = self.create_receipt(
            shop_id=self.shop_a.pk, receipt_number="R-001",
            kind="sale", payment_method="cash", client_name="",
            total=Decimal("1500"), amount_paid=Decimal("1500"),
            cash_amount=Decimal("1500"), mpesa_amount=Decimal("0"),
            status="confirmed",
        )
        ShopReceiptLine.objects.create(
            receipt_id=self.receipt.pk, item_id=item.pk,
            item_name="Sugar 1kg", quantity=Decimal("10"),
            returned_quantity=Decimal("0"), unit_price=Decimal("150"),
            line_total=Decimal("1500"),
        )

    def test_bot_conversation_created_with_welcome(self):
        convo = services.get_or_create_bot(self.shop_a)
        self.assertEqual(convo.kind, ConversationKind.BOT)
        self.assertEqual(convo.messages.count(), 1)
        self.assertTrue(convo.messages.first().is_bot)

    def test_send_gets_bot_reply(self):
        convo = services.get_or_create_bot(self.shop_a)
        resp = self.client.post(
            reverse("chat:api_send", args=[convo.pk]),
            data='{"body": "sales today"}',
            content_type="application/json",
        )
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertIn("reply", data)
        self.assertIn("KSh 1,500.00", data["reply"]["body"])

    def test_sales_intent(self):
        reply = reply_for(self.shop_a, "how much did we sell today")
        self.assertIn("KSh 1,500.00", reply)

    def test_sales_uses_analytics_receipt_default(self):
        """Default company setting filters sales; message can override."""
        CompanyAnalyticsSettings.objects.filter(pk=1).update(
            receipt_data_source="confirmed"
        )
        self.create_receipt(
            shop_id=self.shop_a.pk, receipt_number="R-PEND",
            kind="sale", payment_method="cash", client_name="",
            total=Decimal("900"), amount_paid=Decimal("900"),
            cash_amount=Decimal("900"), mpesa_amount=Decimal("0"),
            status="active",
        )
        # Default confirmed → only the confirmed R-001 from setUp.
        reply = reply_for(self.shop_a, "sales today")
        self.assertIn("KSh 1,500.00", reply)
        self.assertIn("confirmed receipts", reply)
        self.assertNotIn("KSh 2,400.00", reply)

        # Explicit pending override → only the active receipt.
        pending = reply_for(self.shop_a, "sales today pending")
        self.assertIn("KSh 900.00", pending)
        self.assertIn("pending receipts", pending)
        self.assertNotIn("KSh 1,500.00", pending)

        # Explicit confirmed still works.
        confirmed = reply_for(self.shop_a, "sales today confirmed")
        self.assertIn("KSh 1,500.00", confirmed)
        self.assertIn("confirmed receipts", confirmed)

    def test_items_sold_respects_receipt_status(self):
        CompanyAnalyticsSettings.objects.filter(pk=1).update(
            receipt_data_source="pending"
        )
        # Confirmed receipt from setUp should be excluded under pending default.
        reply = reply_for(self.shop_a, "items sold today")
        self.assertIn("pending receipts", reply)
        self.assertIn("No items sold", reply)

        reply = reply_for(self.shop_a, "items sold today confirmed")
        self.assertIn("Sugar 1kg", reply)
        self.assertIn("confirmed receipts", reply)

    def test_stock_intent(self):
        reply = reply_for(self.shop_a, "stock sugar")
        self.assertIn("Sugar 1kg", reply)
        self.assertIn("*3*", reply)
        self.assertIn("KSh 150.00", reply)

    def test_low_stock_intent(self):
        reply = reply_for(self.shop_a, "low stock")
        self.assertIn("Sugar 1kg", reply)

    def test_top_sellers_intent(self):
        reply = reply_for(self.shop_a, "top sellers")
        self.assertIn("Sugar 1kg", reply)

    def test_receipt_lookup(self):
        reply = reply_for(self.shop_a, "receipt R-001")
        self.assertIn("R-001", reply)
        self.assertIn("KSh 1,500.00", reply)

    def test_credit_window_filter(self):
        r = self.create_receipt(
            shop_id=self.shop_a.pk, receipt_number="C-002",
            kind="credit", payment_method="none", client_name="Joe",
            total=Decimal("1000"), amount_paid=Decimal("400"),
            cash_amount=Decimal("0"), mpesa_amount=Decimal("0"),
            status="confirmed",
        )
        ShopReceipt.objects.filter(pk=r.pk).update(
            created_at=timezone.now() - timedelta(days=400)
        )
        # Bare "credits" stays all-time: the old balance still shows.
        self.assertIn("KSh 600.00", reply_for(self.shop_a, "credits"))
        # A window containing the issue date shows it, labelled.
        reply = reply_for(self.shop_a, "credits 1 Jan 2025 to 31 Dec 2025")
        self.assertIn("KSh 600.00", reply)
        self.assertIn("issued 01 Jan 2025", reply)
        # A window that excludes it reports nothing.
        self.assertIn(
            "No outstanding credit", reply_for(self.shop_a, "credits today")
        )

    def test_help_intent(self):
        reply = reply_for(self.shop_a, "help")
        self.assertIn("sales today", reply.lower())

    def test_greeting(self):
        reply = reply_for(self.shop_a, "hello")
        self.assertIn("Alpha Store", reply)

    def test_fallback(self):
        reply = reply_for(self.shop_a, "xyzzy plover")
        self.assertIn("help", reply.lower())


class PendingConfirmTests(ChatTestCase):
    """Staff acting on a shop they aren't allocated to: stock-in and
    transfers send a pending-confirmation card which a shop member's staff
    code executes; sales, credit sales and quotations complete directly
    under the acting employee's account."""

    EMPLOYEE_DDL = (
        "CREATE TABLE IF NOT EXISTS employees_employeeprofile ("
        "id INTEGER PRIMARY KEY, employee_id VARCHAR(12) NOT NULL, "
        "user_id BIGINT, role VARCHAR(32) NOT NULL, "
        "status VARCHAR(16) NOT NULL)",
        "CREATE TABLE IF NOT EXISTS "
        "employees_employeeprofile_assigned_shops ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "employeeprofile_id BIGINT NOT NULL, shop_id BIGINT NOT NULL)",
    )

    def setUp(self):
        super().setUp()
        from django.contrib.auth.models import User

        self.prepare_employee_fixtures()
        manager_user = User.objects.create_user(username="manager")
        member_user = User.objects.create_user(
            username="member1", first_name="Member", last_name="One"
        )
        # Acting employee: a manager NOT allocated to shop_a.
        insert_employee_profile(
            pk=1, employee_id="100001", user_id=manager_user.pk,
            role="company_manager", status="active",
        )
        # Shop member: allocated to shop_a, code 654321.
        insert_employee_profile(
            pk=2, employee_id="654321", user_id=member_user.pk,
            role="shop_attendant", status="active",
        )
        assign_employee_shop(employee_pk=2, shop_id=self.shop_a.pk)
        self.item = Item.objects.create(
            category="Grocery", name="Sugar 1kg",
            minimum_selling_price=Decimal("100"),
            shop_price=Decimal("150"), stock=Decimal("40"),
            low_stock_notify=False, low_stock_threshold=0,
            is_suspended=False,
        )
        self.stock = ShopStock.objects.create(
            shop_id=self.shop_a.pk, item_id=self.item.pk,
            quantity=Decimal("10"), low_stock_threshold=5,
            average_cost=Decimal("80"),
        )
        # Sign in as the non-member manager operating shop_a.
        session = self.client.session
        session["chatbot_employee_id"] = 1
        session["chatbot_shop_id"] = self.shop_a.pk
        session.save()

    def sale_payload(self, **over):
        data = {
            "kind": "sale",
            "payment_method": "cash",
            "lines": [
                {"item_id": self.item.pk, "quantity": "2", "serials": []}
            ],
        }
        data.update(over)
        return data

    def stockin_payload(self, **over):
        data = {
            "supplier_name": "ACME Traders",
            "supplier_phone": "0712 345 678",
            "lines": [
                {"item_id": self.item.pk, "quantity": "2", "serials": []}
            ],
        }
        data.update(over)
        return data

    def pending_message(self):
        for msg in Message.objects.order_by("-id"):
            pa = (msg.meta or {}).get("pending_action") or {}
            if pa.get("status") == "pending":
                return msg, pa
        return None, {}

    def test_nonmember_sale_executes_directly(self):
        """Sales are exempt from member confirmation — the signed-in staff
        account signs the receipt when no code is typed."""
        resp = self.client.post(
            reverse("chat:api_sale"),
            data=json.dumps(self.sale_payload()),
            content_type="application/json",
        )
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertNotIn("pending", data)
        receipt = ShopReceipt.objects.get()
        self.assertEqual(receipt.created_by_id, 1)
        self.assertEqual(receipt.status, "active")
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal("8"))

    def test_nonmember_credit_executes_directly(self):
        resp = self.client.post(
            reverse("chat:api_sale"),
            data=json.dumps(
                self.sale_payload(
                    kind="credit",
                    client_name="Joe",
                    credit_due_date="2099-01-01",
                )
            ),
            content_type="application/json",
        )
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertNotIn("pending", data)
        receipt = ShopReceipt.objects.get()
        self.assertEqual(receipt.kind, "credit")
        self.assertEqual(receipt.created_by_id, 1)

    def test_nonmember_quotation_executes_directly(self):
        resp = self.client.post(
            reverse("chat:api_sale"),
            data=json.dumps(
                self.sale_payload(kind="quotation", client_name="Joe")
            ),
            content_type="application/json",
        )
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertNotIn("pending", data)
        receipt = ShopReceipt.objects.get()
        self.assertEqual(receipt.kind, "quotation")
        # Quotations never touch stock.
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal("10"))

    def test_nonmember_own_code_executes(self):
        """A typed code only needs to be an active employee — it no longer
        has to belong to a shop member for sales."""
        resp = self.client.post(
            reverse("chat:api_sale"),
            data=json.dumps(self.sale_payload(staff_code="100001")),
            content_type="application/json",
        )
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertNotIn("pending", data)
        self.assertEqual(ShopReceipt.objects.get().created_by_id, 1)

    def test_member_code_executes_immediately(self):
        resp = self.client.post(
            reverse("chat:api_sale"),
            data=json.dumps(self.sale_payload(staff_code="654321")),
            content_type="application/json",
        )
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertNotIn("pending", data)
        receipt = ShopReceipt.objects.get()
        self.assertEqual(receipt.created_by_id, 2)

    def test_nonmember_stockin_posts_pending_card(self):
        resp = self.client.post(
            reverse("chat:api_stock_in"),
            data=json.dumps(self.stockin_payload()),
            content_type="application/json",
        )
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertTrue(data["pending"])
        # Nothing was written — the stock-in waits for a member's code.
        self.assertEqual(StockMovement.objects.count(), 0)
        msg, pa = self.pending_message()
        self.assertIsNotNone(msg)
        self.assertEqual(pa["action"], "stockin")
        self.assertIn("payload", pa)  # stored server-side
        # …but stripped from the serialized card the client receives.
        sent = data["message"]["meta"]["pending_action"]
        self.assertNotIn("payload", sent)
        self.assertEqual(sent["requested_by"], "manager")
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal("10"))

    def test_confirm_executes_with_member_details(self):
        self.client.post(
            reverse("chat:api_stock_in"),
            data=json.dumps(self.stockin_payload()),
            content_type="application/json",
        )
        msg, _ = self.pending_message()
        resp = self.client.post(
            reverse("chat:api_pending_confirm", args=[msg.pk]),
            data=json.dumps({"staff_code": "654321"}),
            content_type="application/json",
        )
        data = resp.json()
        self.assertTrue(data["ok"])
        movement = StockMovement.objects.get()
        self.assertEqual(movement.created_by_id, 2)
        self.assertEqual(movement.movement_type, "in")
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal("12"))
        # The card becomes the real stock-in card, stamped with the member.
        meta = data["message"]["meta"]
        self.assertIn("stock_in", meta)
        self.assertNotIn("pending_action", meta)
        self.assertEqual(meta["confirmed"]["by"], "Member One")

    def test_confirm_rejects_nonmember_code(self):
        self.client.post(
            reverse("chat:api_stock_in"),
            data=json.dumps(self.stockin_payload()),
            content_type="application/json",
        )
        msg, _ = self.pending_message()
        # The manager's own code is active but not a shop member.
        resp = self.client.post(
            reverse("chat:api_pending_confirm", args=[msg.pk]),
            data=json.dumps({"staff_code": "100001"}),
            content_type="application/json",
        )
        self.assertFalse(resp.json()["ok"])
        self.assertEqual(StockMovement.objects.count(), 0)
        msg.refresh_from_db()
        self.assertEqual(
            msg.meta["pending_action"]["status"], "pending"
        )

    def test_second_confirm_fails(self):
        self.client.post(
            reverse("chat:api_stock_in"),
            data=json.dumps(self.stockin_payload()),
            content_type="application/json",
        )
        msg, _ = self.pending_message()
        url = reverse("chat:api_pending_confirm", args=[msg.pk])
        self.client.post(
            url,
            data=json.dumps({"staff_code": "654321"}),
            content_type="application/json",
        )
        resp = self.client.post(
            url,
            data=json.dumps({"staff_code": "654321"}),
            content_type="application/json",
        )
        self.assertFalse(resp.json()["ok"])
        self.assertEqual(StockMovement.objects.count(), 1)

    def test_pending_transfer_confirms_into_direct_convo(self):
        resp = self.client.post(
            reverse("chat:api_transfers"),
            data=json.dumps(
                {
                    "destination_shop_id": self.shop_b.pk,
                    "lines": [
                        {
                            "item_id": self.item.pk,
                            "quantity": "3",
                            "serials": [],
                        }
                    ],
                }
            ),
            content_type="application/json",
        )
        self.assertTrue(resp.json()["pending"])
        self.assertEqual(StockMovement.objects.count(), 0)
        msg, pa = self.pending_message()
        self.assertEqual(pa["action"], "transfer")

        resp = self.client.post(
            reverse("chat:api_pending_confirm", args=[msg.pk]),
            data=json.dumps({"staff_code": "654321"}),
            content_type="application/json",
        )
        data = resp.json()
        self.assertTrue(data["ok"])
        movement = StockMovement.objects.get()
        self.assertEqual(movement.request_status, "pending")
        self.assertEqual(movement.created_by_id, 2)
        self.assertEqual(movement.requested_from_shop_id, self.shop_b.pk)
        # The real transfer card was posted into the direct conversation.
        card_msg = Message.objects.get(client_tag=f"transfer:{movement.pk}")
        self.assertEqual(card_msg.meta["transfer"]["status"], "pending")
        self.assertEqual(data["card_conversation_id"], card_msg.conversation_id)


class TransferBlindTests(ChatTestCase):
    """The receiving shop counts what arrived blind — the sent quantities
    (and serial values) stay hidden until they accept and stock moves."""

    def setUp(self):
        super().setUp()
        self.item = Item.objects.create(
            category="Grocery", name="Sugar 1kg",
            minimum_selling_price=Decimal("100"),
            shop_price=Decimal("150"), stock=Decimal("40"),
            low_stock_notify=False, low_stock_threshold=0,
            is_suspended=False,
        )
        ShopStock.objects.create(
            shop_id=self.shop_a.pk, item_id=self.item.pk,
            quantity=Decimal("10"), low_stock_threshold=5,
            average_cost=Decimal("80"),
        )
        ShopStock.objects.create(
            shop_id=self.shop_b.pk, item_id=self.item.pk,
            quantity=Decimal("0"), low_stock_threshold=5,
            average_cost=Decimal("80"),
        )

    def _posted_card(self):
        from . import stock as stock_ops

        movement, _ = stock_ops.create_transfer(
            self.shop_a,
            self.shop_b,
            [{"item_id": self.item.pk, "quantity": "3", "serials": []}],
            employee_id=0,
        )
        lines = list(
            stock_ops.StockMovementLine.objects.filter(
                movement_id=movement.pk
            ).order_by("pk")
        )
        convo = services.get_or_create_direct(self.shop_a, self.shop_b)
        meta = stock_ops.transfer_card(
            movement, self.shop_a, self.shop_b, lines
        )
        msg = services.post_card(
            convo,
            self.shop_a,
            "Stock transfer → Beta Mart: 3× Sugar 1kg",
            meta,
        )
        return msg, movement, convo

    def test_receiver_serialized_card_is_blind(self):
        msg, movement, _ = self._posted_card()
        data = services.serialize_message(msg, self.shop_b, None)
        t = data["meta"]["transfer"]
        self.assertTrue(t["blind"])
        self.assertEqual(t["items"][0]["qty"], "")
        self.assertEqual(t["items"][0]["serial_count"], 0)
        # …and the real figures are not sitting in the message body either.
        self.assertNotIn("3×", data["body"])

    def test_sender_view_keeps_quantities(self):
        msg, movement, _ = self._posted_card()
        data = services.serialize_message(msg, self.shop_a, None)
        t = data["meta"]["transfer"]
        self.assertNotIn("blind", t)
        self.assertEqual(t["items"][0]["qty"], "3.000")
        self.assertIn("3×", data["body"])

    def test_fulfilled_transfer_reveals_quantities(self):
        from . import stock as stock_ops

        msg, movement, _ = self._posted_card()
        line = StockMovementLine.objects.get(movement_id=movement.pk)
        stock_ops.respond_to_transfer(
            self.shop_b, movement.pk, "accept", {str(line.pk): "3"},
            employee_id=0,
        )
        # Mirror the view: the card meta gets the new status.
        meta = dict(msg.meta)
        transfer = dict(meta["transfer"])
        transfer["status"] = "fulfilled"
        meta["transfer"] = transfer
        msg.meta = meta
        msg.save(update_fields=["meta"])
        data = services.serialize_message(msg, self.shop_b, None)
        t = data["meta"]["transfer"]
        self.assertNotIn("blind", t)
        self.assertEqual(t["items"][0]["qty"], "3.000")

    def test_chat_preview_hides_quantities(self):
        msg, movement, convo = self._posted_card()
        data = services.serialize_conversation(
            convo, self.shop_b, {self.shop_a.pk: self.shop_a}
        )
        preview = data["last_message"]["preview"]
        self.assertNotIn("3×", preview)
        self.assertIn("Incoming stock transfer", preview)

    def test_last_message_card_flags(self):
        # Notification sounds key off these: cards vs plain chat.
        msg, movement, convo = self._posted_card()
        data = services.serialize_conversation(
            convo, self.shop_b, {self.shop_a.pk: self.shop_a}
        )
        self.assertEqual(data["last_message"]["kind"], "text")
        self.assertEqual(data["last_message"]["card"], "transfer")

        services.post_text(convo, self.shop_a, "plain hello", "")
        data = services.serialize_conversation(
            convo, self.shop_b, {self.shop_a.pk: self.shop_a}
        )
        self.assertEqual(data["last_message"]["card"], "")


class TransferRespondCodeTests(ChatTestCase):
    """The receiving shop accepts or declines with a member's 6-digit
    staff code — the movement stores responded_by_id and the card/note
    name the employee who handled it."""

    EMPLOYEE_DDL = PendingConfirmTests.EMPLOYEE_DDL

    def setUp(self):
        super().setUp()
        from django.contrib.auth.models import User

        self.prepare_employee_fixtures()
        member_user = User.objects.create_user(
            username="memberb", first_name="Beta", last_name="Member"
        )
        outsider_user = User.objects.create_user(username="outsider")
        # Member of the receiving shop (shop_b).
        insert_employee_profile(
            pk=2, employee_id="654321", user_id=member_user.pk,
            role="shop_attendant", status="active",
        )
        assign_employee_shop(employee_pk=2, shop_id=self.shop_b.pk)
        # Active employee not allocated to shop_b.
        insert_employee_profile(
            pk=3, employee_id="100001", user_id=outsider_user.pk,
            role="company_manager", status="active",
        )
        self.item = Item.objects.create(
            category="Grocery", name="Sugar 1kg",
            minimum_selling_price=Decimal("100"),
            shop_price=Decimal("150"), stock=Decimal("40"),
            low_stock_notify=False, low_stock_threshold=0,
            is_suspended=False,
        )
        ShopStock.objects.create(
            shop_id=self.shop_a.pk, item_id=self.item.pk,
            quantity=Decimal("10"), low_stock_threshold=5,
            average_cost=Decimal("80"),
        )
        ShopStock.objects.create(
            shop_id=self.shop_b.pk, item_id=self.item.pk,
            quantity=Decimal("0"), low_stock_threshold=5,
            average_cost=Decimal("80"),
        )
        self.login(code="222222", password="pass-b")

    def _make_transfer(self):
        from . import stock as stock_ops

        movement, lines = stock_ops.create_transfer(
            self.shop_a,
            self.shop_b,
            [{"item_id": self.item.pk, "quantity": "3", "serials": []}],
            employee_id=0,
        )
        movement_lines = list(
            StockMovementLine.objects.filter(
                movement_id=movement.pk
            ).order_by("pk")
        )
        convo = services.get_or_create_direct(self.shop_a, self.shop_b)
        msg = services.post_card(
            convo,
            self.shop_a,
            "Stock transfer → Beta Mart: 3× Sugar 1kg",
            stock_ops.transfer_card(
                movement, self.shop_a, self.shop_b, movement_lines
            ),
        )
        msg.client_tag = f"transfer:{movement.pk}"
        msg.save(update_fields=["client_tag"])
        return movement, movement_lines[0], convo

    def _respond(self, movement, payload):
        return self.client.post(
            reverse("chat:api_transfer_respond", args=[movement.pk]),
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_accept_without_code_is_rejected(self):
        movement, line, _ = self._make_transfer()
        resp = self._respond(
            movement,
            {"decision": "accept", "received": {str(line.pk): "3"}},
        )
        self.assertFalse(resp.json()["ok"])
        self.assertIn("member of this shop", resp.json()["error"])
        movement.refresh_from_db()
        self.assertEqual(movement.request_status, "pending")
        self.assertIsNone(movement.responded_by_id)

    def test_non_member_code_is_rejected(self):
        movement, line, _ = self._make_transfer()
        resp = self._respond(
            movement,
            {
                "decision": "accept",
                "received": {str(line.pk): "3"},
                "staff_code": "100001",
            },
        )
        self.assertFalse(resp.json()["ok"])
        self.assertIn("member of this shop", resp.json()["error"])
        movement.refresh_from_db()
        self.assertEqual(movement.request_status, "pending")

    def test_accept_records_responder(self):
        movement, line, convo = self._make_transfer()
        resp = self._respond(
            movement,
            {
                "decision": "accept",
                "received": {str(line.pk): "3"},
                "staff_code": "654321",
            },
        )
        data = resp.json()
        self.assertTrue(data["ok"])
        movement.refresh_from_db()
        self.assertEqual(movement.request_status, "fulfilled")
        self.assertEqual(movement.responded_by_id, 2)
        self.assertEqual(
            data["card_meta"]["transfer"]["responded_by"], "Beta Member"
        )
        note = Message.objects.filter(conversation=convo).order_by("-id")[0]
        self.assertIn("by Beta Member", note.body)
        # The received stock actually landed at shop_b.
        self.assertEqual(
            ShopStock.objects.get(
                shop_id=self.shop_b.pk, item_id=self.item.pk
            ).quantity,
            Decimal("3.000"),
        )

    def test_decline_records_responder(self):
        movement, line, convo = self._make_transfer()
        resp = self._respond(
            movement,
            {
                "decision": "decline",
                "reason": "wrong items sent",
                "staff_code": "654321",
            },
        )
        data = resp.json()
        self.assertTrue(data["ok"])
        movement.refresh_from_db()
        self.assertEqual(movement.request_status, "declined")
        self.assertEqual(movement.responded_by_id, 2)
        self.assertEqual(movement.response_note, "wrong items sent")
        note = Message.objects.filter(conversation=convo).order_by("-id")[0]
        self.assertIn("declined by Beta Member", note.body)


class GroupTests(ChatTestCase):
    def setUp(self):
        super().setUp()
        self.shop_c = Shop.objects.create(
            name="Gamma Foods", location="Kisumu", email="c@x.co",
            phone_number="0700000003", login_code="333333",
            password_hash=make_password("pass-c"), is_suspended=False,
            is_hidden=False,
        )
        self.login()

    def create_group(self, name="Branch group", members=None):
        members = members if members is not None else [
            self.shop_b.pk, self.shop_c.pk
        ]
        return self.client.post(
            reverse("chat:api_group_create"),
            data=json.dumps({"name": name, "members": members}),
            content_type="application/json",
        )

    def test_create_group(self):
        resp = self.create_group()
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        convo = data["conversation"]
        self.assertEqual(convo["kind"], "group")
        self.assertEqual(convo["title"], "Branch group")
        self.assertIsNone(convo["peer_shop_id"])
        self.assertIn("Beta Mart", convo["subtitle"])
        self.assertIn("Gamma Foods", convo["subtitle"])
        # The creator sees the system message immediately.
        msgs = data["active"]["messages"]
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["kind"], "system")
        self.assertIn("created group", msgs[0]["body"])

    def test_create_group_requires_name(self):
        resp = self.create_group(name="  ")
        self.assertEqual(resp.status_code, 400)

    def test_create_group_requires_members(self):
        resp = self.create_group(members=[])
        self.assertEqual(resp.status_code, 400)

    def test_create_group_skips_self_and_duplicates(self):
        resp = self.create_group(
            members=[self.shop_a.pk, self.shop_b.pk, self.shop_b.pk]
        )
        self.assertTrue(resp.json()["ok"])
        convo_id = resp.json()["conversation"]["id"]
        convo = services.get_member_conversation(convo_id, self.shop_a)
        self.assertEqual(convo.participants.count(), 2)

    def test_group_send_and_poll(self):
        convo_id = self.create_group().json()["conversation"]["id"]
        resp = self.client.post(
            reverse("chat:api_send", args=[convo_id]),
            data='{"body": "Hello all"}',
            content_type="application/json",
        )
        self.assertTrue(resp.json()["ok"])

        # Both other members see the group, the sender label and unread.
        client_b = Client()
        client_b.post(
            reverse("chat:login"),
            {"login_code": "222222", "password": "pass-b"},
        )
        resp = client_b.get(
            reverse("chat:api_poll"), {"conversation_id": convo_id}
        )
        data = resp.json()
        convos = {c["id"]: c for c in data["conversations"]}
        self.assertEqual(convos[convo_id]["title"], "Branch group")
        # System notice + the message = 2 unread for a fresh member.
        self.assertEqual(convos[convo_id]["unread"], 2)
        msgs = data["active"]["messages"]
        self.assertEqual(msgs[-1]["body"], "Hello all")
        self.assertEqual(msgs[-1]["sender_name"], "Alpha Store")
        self.assertFalse(msgs[-1]["mine"])

        client_c = Client()
        client_c.post(
            reverse("chat:login"),
            {"login_code": "333333", "password": "pass-c"},
        )
        resp = client_c.get(
            reverse("chat:api_poll"), {"conversation_id": convo_id}
        )
        self.assertEqual(
            resp.json()["active"]["messages"][-1]["sender_name"], "Alpha Store"
        )

    def test_group_ticks_read_when_all_read(self):
        convo_id = self.create_group().json()["conversation"]["id"]
        self.client.post(
            reverse("chat:api_send", args=[convo_id]),
            data='{"body": "hi"}',
            content_type="application/json",
        )
        last_id = Message.objects.order_by("-id").first().pk
        for code, password in (
            ("222222", "pass-b"), ("333333", "pass-c")
        ):
            c = Client()
            c.post(
                reverse("chat:login"),
                {"login_code": code, "password": password},
            )
            c.post(
                reverse("chat:api_read", args=[convo_id]),
                data=json.dumps({"up_to_id": last_id}),
                content_type="application/json",
            )
        resp = self.client.get(
            reverse("chat:api_poll"), {"conversation_id": convo_id}
        )
        msgs = resp.json()["active"]["messages"]
        self.assertEqual(msgs[-1]["status"], "read")

    def test_group_send_rejected_for_non_member(self):
        convo_id = self.create_group(
            members=[self.shop_b.pk]
        ).json()["conversation"]["id"]
        client_c = Client()
        client_c.post(
            reverse("chat:login"),
            {"login_code": "333333", "password": "pass-c"},
        )
        resp = client_c.post(
            reverse("chat:api_send", args=[convo_id]),
            data='{"body": "intruder"}',
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 404)

    def test_rename_group(self):
        convo_id = self.create_group().json()["conversation"]["id"]
        resp = self.client.post(
            reverse("chat:api_group_update", args=[convo_id]),
            data=json.dumps({"name": "Renamed branches"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["conversation"]["title"], "Renamed branches")
        self.assertTrue(data["conversation"]["can_manage"])
        note = Message.objects.filter(conversation_id=convo_id).order_by("-id")[0]
        self.assertEqual(note.kind, "system")
        self.assertIn("renamed the group", note.body)
        self.assertIn("Renamed branches", note.body)

    def test_rename_group_requires_name(self):
        convo_id = self.create_group().json()["conversation"]["id"]
        resp = self.client.post(
            reverse("chat:api_group_update", args=[convo_id]),
            data=json.dumps({"name": "  "}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_delete_group(self):
        convo_id = self.create_group().json()["conversation"]["id"]
        resp = self.client.post(
            reverse("chat:api_group_delete", args=[convo_id]),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["ok"])
        self.assertEqual(resp.json()["deleted_id"], convo_id)
        self.assertIsNone(
            services.get_member_conversation(convo_id, self.shop_a)
        )
        # Other members also lose the thread.
        self.assertIsNone(
            services.get_member_conversation(convo_id, self.shop_b)
        )

    def test_group_settings_rejected_for_non_member(self):
        convo_id = self.create_group(
            members=[self.shop_b.pk]
        ).json()["conversation"]["id"]
        client_c = Client()
        client_c.post(
            reverse("chat:login"),
            {"login_code": "333333", "password": "pass-c"},
        )
        resp = client_c.post(
            reverse("chat:api_group_update", args=[convo_id]),
            data=json.dumps({"name": "Hijacked"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 404)
        resp = client_c.post(
            reverse("chat:api_group_delete", args=[convo_id]),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 404)


class QuickStockinTests(ChatTestCase):
    """The `#` quick stock-in posts into the open chat: a pending card
    for a member of the posting shop to confirm with their staff code,
    which executes the stock-in and drops a "received" note in the chat."""

    EMPLOYEE_DDL = PendingConfirmTests.EMPLOYEE_DDL

    def setUp(self):
        super().setUp()
        from django.contrib.auth.models import User

        self.prepare_employee_fixtures()
        member_user = User.objects.create_user(
            username="member1", first_name="Member", last_name="One"
        )
        # Shop member: allocated to shop_a, code 654321.
        insert_employee_profile(
            pk=2, employee_id="654321", user_id=member_user.pk,
            role="shop_attendant", status="active",
        )
        assign_employee_shop(employee_pk=2, shop_id=self.shop_a.pk)
        self.item = Item.objects.create(
            category="Grocery", name="Sugar 1kg",
            minimum_selling_price=Decimal("100"),
            shop_price=Decimal("150"), stock=Decimal("40"),
            low_stock_notify=False, low_stock_threshold=0,
            is_suspended=False,
        )
        self.stock = ShopStock.objects.create(
            shop_id=self.shop_a.pk, item_id=self.item.pk,
            quantity=Decimal("10"), low_stock_threshold=5,
            average_cost=Decimal("80"),
        )
        self.shop_c = Shop.objects.create(
            name="Gamma Foods", location="Kisumu", email="c@x.co",
            phone_number="0700000003", login_code="333333",
            password_hash=make_password("pass-c"), is_suspended=False,
            is_hidden=False,
        )
        self.login()
        self.convo = services.create_group(
            self.shop_a, "Branch group", [self.shop_b, self.shop_c]
        )

    def quickin(self, **over):
        data = {
            "lines": [
                {
                    "item_id": self.item.pk,
                    "quantity": "5",
                    "serials": [],
                    "buying_price": "90",
                }
            ],
        }
        data.update(over)
        return self.client.post(
            reverse("chat:api_quick_stockin", args=[self.convo.pk]),
            data=json.dumps(data),
            content_type="application/json",
        )

    def test_quick_stockin_posts_pending_card_to_chat(self):
        data = self.quickin().json()
        self.assertTrue(data["ok"])
        self.assertTrue(data["pending"])
        self.assertEqual(data["conversation_id"], self.convo.pk)
        msg = Message.objects.get(pk=data["message"]["id"])
        self.assertEqual(msg.conversation_id, self.convo.pk)
        self.assertEqual(msg.sender_shop_id, self.shop_a.pk)
        pa = msg.meta["pending_action"]
        self.assertEqual(pa["action"], "stockin")
        self.assertEqual(pa["shop_id"], self.shop_a.pk)
        self.assertEqual(pa["shop_name"], "Alpha Store")
        self.assertEqual(StockMovement.objects.count(), 0)

    def test_member_code_executes_and_posts_received(self):
        msg_id = self.quickin().json()["message"]["id"]
        resp = self.client.post(
            reverse("chat:api_pending_confirm", args=[msg_id]),
            data='{"staff_code": "654321"}',
            content_type="application/json",
        )
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertIn("note", data)

        # The card became the real stock-in card in place.
        msg = Message.objects.get(pk=msg_id)
        self.assertNotIn("pending_action", msg.meta)
        self.assertIn("stock_in", msg.meta)
        self.assertEqual(msg.meta["confirmed"]["by"], "Member One")

        # Stock moved under the confirming member's id.
        movement = StockMovement.objects.get()
        self.assertEqual(movement.shop_id, self.shop_a.pk)
        self.assertEqual(movement.created_by_id, 2)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal("15"))
        self.item.refresh_from_db()
        self.assertEqual(self.item.stock, Decimal("45"))

        # A "received" note landed in the chat from the action shop.
        note = Message.objects.get(pk=data["note"]["id"])
        self.assertEqual(note.conversation_id, self.convo.pk)
        self.assertEqual(note.sender_shop_id, self.shop_a.pk)
        self.assertIn("Stock received", note.body)
        self.assertIn("Member One", note.body)

    def test_other_shop_member_code_rejected(self):
        msg_id = self.quickin().json()["message"]["id"]
        client_b = Client()
        client_b.post(
            reverse("chat:login"),
            {"login_code": "222222", "password": "pass-b"},
        )
        resp = client_b.post(
            reverse("chat:api_pending_confirm", args=[msg_id]),
            data='{"staff_code": "999999"}',
            content_type="application/json",
        )
        self.assertFalse(resp.json()["ok"])
        self.assertIn("Alpha Store", resp.json()["error"])
        self.assertEqual(StockMovement.objects.count(), 0)

    def test_typed_code_executes_immediately(self):
        data = self.quickin(staff_code="654321").json()
        self.assertTrue(data["ok"])
        self.assertFalse(data["pending"])
        self.assertIn("stock_in", data["message"]["meta"])
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal("15"))

    def test_quickin_requires_lines(self):
        resp = self.quickin(lines=[])
        self.assertFalse(resp.json()["ok"])
        self.assertEqual(Message.objects.count(), 1)  # just the system msg

    def test_quickin_rejected_for_non_member(self):
        outsider = Client()
        outsider.post(
            reverse("chat:login"),
            {"login_code": "333333", "password": "pass-c"},
        )
        # Shop C is in the group; a direct convo it isn't in must 404.
        solo = services.get_or_create_direct(self.shop_a, self.shop_b)
        resp = outsider.post(
            reverse("chat:api_quick_stockin", args=[solo.pk]),
            data=json.dumps({"lines": []}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 404)


class ViewSmokeTests(ChatTestCase):
    def test_app_renders_after_login(self):
        self.login()
        resp = self.client.get(reverse("chat:app"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "wa-app")

    def test_poll_returns_assistant(self):
        self.login()
        resp = self.client.get(reverse("chat:api_poll"))
        convos = resp.json()["conversations"]
        kinds = {c["kind"] for c in convos}
        self.assertIn("bot", kinds)


class TradeTests(ChatTestCase):
    """Trade out / trade in mirror MY-SHOP's checkout and only run when the
    shop's effective POS settings enable them (company defaults merged with
    the shop's override row)."""

    EMPLOYEE_DDL = PendingConfirmTests.EMPLOYEE_DDL

    def setUp(self):
        super().setUp()
        self.prepare_employee_fixtures()
        # Non-member manager (staff session) and a shop member whose
        # code completes stock actions.
        insert_employee_profile(
            pk=1, employee_id="100001", user_id=None,
            role="company_manager", status="active",
        )
        insert_employee_profile(
            pk=2, employee_id="654321", user_id=None,
            role="shop_attendant", status="active",
        )
        assign_employee_shop(employee_pk=2, shop_id=self.shop_a.pk)
        self.item = Item.objects.create(
            category="Grocery", name="Sugar 1kg",
            minimum_selling_price=Decimal("100"),
            shop_price=Decimal("150"), stock=Decimal("40"),
            low_stock_notify=False, low_stock_threshold=0,
            is_suspended=False,
        )
        self.stock = ShopStock.objects.create(
            shop_id=self.shop_a.pk, item_id=self.item.pk,
            quantity=Decimal("10"), low_stock_threshold=5,
            average_cost=Decimal("80"),
        )
        self.login()

    def trade_payload(self, kind="trade_out", **over):
        data = {
            "kind": kind,
            "client_name": "Joe",
            "client_phone": "0712 345 678",
            "staff_code": "654321",
            "lines": [
                {"item_id": self.item.pk, "quantity": "2", "serials": []}
            ],
        }
        data.update(over)
        return data

    def post_trade(self, payload):
        return self.client.post(
            reverse("chat:api_sale"),
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_trade_out_moves_stock_and_writes_receipt(self):
        data = self.post_trade(self.trade_payload()).json()
        self.assertTrue(data["ok"])
        receipt = ShopReceipt.objects.get()
        self.assertEqual(receipt.kind, "trade_out")
        # Open trade — MY-SHOP settles it later via payment or exchange.
        self.assertEqual(receipt.status, "active")
        self.assertTrue(receipt.receipt_number.startswith("T"))
        self.assertEqual(receipt.client_name, "JOE")
        self.assertEqual(receipt.amount_paid, Decimal("0"))
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal("8"))
        movement = StockMovement.objects.get()
        self.assertEqual(movement.movement_type, "out")
        self.assertEqual(movement.entry_source, "trade_out")
        self.assertEqual(movement.created_by_id, 2)

    def test_trade_in_stocks_in_with_owed_price(self):
        data = self.post_trade(
            self.trade_payload(
                kind="trade_in",
                lines=[
                    {
                        "item_id": self.item.pk,
                        "quantity": "4",
                        "serials": [],
                        "buying_price": "70",
                    }
                ],
            )
        ).json()
        self.assertTrue(data["ok"])
        receipt = ShopReceipt.objects.get()
        self.assertEqual(receipt.kind, "trade_in")
        self.assertTrue(receipt.receipt_number.startswith("TI"))
        self.assertEqual(receipt.total, Decimal("280"))
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal("14"))
        movement = StockMovement.objects.get()
        self.assertEqual(movement.movement_type, "in")
        self.assertEqual(movement.entry_source, "trade_in")

    def test_trade_in_requires_owed_price(self):
        resp = self.post_trade(self.trade_payload(kind="trade_in"))
        self.assertFalse(resp.json()["ok"])
        self.assertIn("amount owed", resp.json()["error"])

    def test_trade_requires_client(self):
        resp = self.post_trade(self.trade_payload(client_name=""))
        self.assertFalse(resp.json()["ok"])
        resp = self.post_trade(self.trade_payload(client_phone=""))
        self.assertFalse(resp.json()["ok"])

    def test_trade_disabled_by_company_settings(self):
        CompanyPosSettings.objects.create(
            id=1, enable_trade_out=False, enable_trade_in=False
        )
        for kind in ("trade_out", "trade_in"):
            resp = self.post_trade(self.trade_payload(kind=kind))
            self.assertFalse(resp.json()["ok"])
            self.assertIn("disabled", resp.json()["error"])
        self.assertEqual(ShopReceipt.objects.count(), 0)

    def test_shop_override_disables_one_kind(self):
        CompanyPosSettings.objects.create(id=1)
        ShopPosSettings.objects.create(
            shop_id=self.shop_a.pk, override_pos=True,
            enable_trade_out=False, enable_trade_in=True,
        )
        resp = self.post_trade(self.trade_payload(kind="trade_out"))
        self.assertFalse(resp.json()["ok"])
        resp = self.post_trade(
            self.trade_payload(
                kind="trade_in",
                lines=[
                    {
                        "item_id": self.item.pk,
                        "quantity": "1",
                        "serials": [],
                        "buying_price": "70",
                    }
                ],
            )
        )
        self.assertTrue(resp.json()["ok"])

    def test_trade_fab_hidden_when_disabled(self):
        resp = self.client.get(reverse("chat:app"))
        self.assertContains(resp, "data-fab-others-toggle")
        self.assertContains(resp, 'data-fab="trade_out"')
        self.assertContains(resp, 'data-fab="trade_in"')
        CompanyPosSettings.objects.create(
            id=1, enable_trade_out=False, enable_trade_in=False
        )
        resp = self.client.get(reverse("chat:app"))
        self.assertContains(resp, "data-fab-others-toggle")
        self.assertContains(resp, 'data-fab="transfer"')
        self.assertNotContains(resp, 'data-fab="trade_out"')
        self.assertNotContains(resp, 'data-fab="trade_in"')

    def test_nonmember_trade_posts_pending_card(self):
        """Staff not allocated to the shop send trades for confirmation —
        same gate as stock-in and transfers."""
        session = self.client.session
        session["chatbot_employee_id"] = 1
        session.save()
        data = self.post_trade(self.trade_payload(staff_code="")).json()
        self.assertTrue(data["ok"])
        self.assertTrue(data["pending"])
        self.assertEqual(ShopReceipt.objects.count(), 0)
        msg = Message.objects.order_by("-id").first()
        data = self.client.post(
            reverse("chat:api_pending_confirm", args=[msg.pk]),
            data=json.dumps({"staff_code": "654321"}),
            content_type="application/json",
        ).json()
        self.assertTrue(data["ok"])
        self.assertEqual(ShopReceipt.objects.get().kind, "trade_out")
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal("8"))


class ExpenseTests(ChatTestCase):
    """Expense registration mirrors MY-SHOP's register_shop_expense and is
    gated like stock-in/transfers for non-member staff."""

    EMPLOYEE_DDL = PendingConfirmTests.EMPLOYEE_DDL

    def setUp(self):
        super().setUp()
        self.prepare_employee_fixtures()
        insert_employee_profile(
            pk=1, employee_id="100001", user_id=None,
            role="company_manager", status="active",
        )
        insert_employee_profile(
            pk=2, employee_id="654321", user_id=None,
            role="shop_attendant", status="active",
        )
        assign_employee_shop(employee_pk=2, shop_id=self.shop_a.pk)
        self.login()

    def expense_payload(self, **over):
        data = {
            "category": "transport",
            "name": "Fuel",
            "amount": "500",
            "payment_status": "paid",
            "supplier_name": "Shell Station",
            "supplier_phone": "0712 345 678",
            "staff_code": "654321",
        }
        data.update(over)
        return data

    def post_expense(self, payload):
        return self.client.post(
            reverse("chat:api_expense"),
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_paid_expense_writes_row_and_supplier(self):
        data = self.post_expense(self.expense_payload()).json()
        self.assertTrue(data["ok"])
        expense = Expense.objects.get()
        self.assertEqual(expense.name, "FUEL")
        self.assertEqual(expense.category, "transport")
        self.assertEqual(expense.amount, Decimal("500.00"))
        self.assertEqual(expense.amount_paid, Decimal("500.00"))
        self.assertEqual(expense.cash_amount, Decimal("500.00"))
        self.assertEqual(expense.payment_status, "paid")
        self.assertEqual(expense.created_by_id, 2)
        supplier = ExpenseSupplier.objects.get()
        self.assertEqual(supplier.name, "SHELL STATION")
        self.assertEqual(expense.supplier_id, supplier.pk)

    def test_multi_line_expense_cart(self):
        """Multiple parked lines share one supplier + payment status."""
        data = self.post_expense(
            {
                "lines": [
                    {
                        "category": "rent",
                        "name": "Meru pork",
                        "amount": "10000",
                    },
                    {
                        "category": "utilities",
                        "name": "Power",
                        "amount": "2500",
                    },
                ],
                "payment_status": "paid",
                "supplier_name": "Landlord",
                "supplier_phone": "0712 345 678",
                "staff_code": "654321",
            }
        ).json()
        self.assertTrue(data["ok"])
        rows = list(Expense.objects.order_by("id"))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].name, "MERU PORK")
        self.assertEqual(rows[0].amount, Decimal("10000.00"))
        self.assertEqual(rows[1].name, "POWER")
        self.assertEqual(rows[1].amount, Decimal("2500.00"))
        self.assertEqual(rows[0].supplier_name, rows[1].supplier_name)
        card = data["message"]["meta"]["expense"]
        self.assertEqual(len(card["items"]), 2)
        self.assertEqual(card["amount"], "12,500.00")
        self.assertIn("2 expenses", data["message"]["body"])

    def test_unpaid_expense_leaves_amount_paid_zero(self):
        data = self.post_expense(
            self.expense_payload(payment_status="unpaid")
        ).json()
        self.assertTrue(data["ok"])
        expense = Expense.objects.get()
        self.assertEqual(expense.amount_paid, Decimal("0"))
        self.assertEqual(expense.payment_status, "unpaid")

    def test_expense_requires_supplier_and_valid_fields(self):
        resp = self.post_expense(self.expense_payload(supplier_name=""))
        self.assertFalse(resp.json()["ok"])
        resp = self.post_expense(self.expense_payload(supplier_phone=""))
        self.assertFalse(resp.json()["ok"])
        resp = self.post_expense(self.expense_payload(category=""))
        self.assertFalse(resp.json()["ok"])
        resp = self.post_expense(self.expense_payload(amount="0"))
        self.assertFalse(resp.json()["ok"])
        self.assertEqual(Expense.objects.count(), 0)

    def test_expense_requires_staff_code(self):
        resp = self.post_expense(self.expense_payload(staff_code=""))
        self.assertFalse(resp.json()["ok"])

    def test_nonmember_expense_posts_pending_card(self):
        session = self.client.session
        session["chatbot_employee_id"] = 1
        session.save()
        data = self.post_expense(self.expense_payload(staff_code="")).json()
        self.assertTrue(data["ok"])
        self.assertTrue(data["pending"])
        self.assertEqual(Expense.objects.count(), 0)
        msg = Message.objects.order_by("-id").first()
        data = self.client.post(
            reverse("chat:api_pending_confirm", args=[msg.pk]),
            data=json.dumps({"staff_code": "654321"}),
            content_type="application/json",
        ).json()
        self.assertTrue(data["ok"])
        expense = Expense.objects.get()
        self.assertEqual(expense.created_by_id, 2)
        self.assertIn("expense", data["message"]["meta"])


class ActivityFeedTests(ChatTestCase):
    """Completed stock-in/transfers/trades/expenses also post a card into
    shared all-shops feed groups (Incoming stock / Transfers / Trades /
    Expenses) so everyone can track the day's activity — each feed card
    names the staff member who ran the action (`meta.by`)."""

    EMPLOYEE_DDL = PendingConfirmTests.EMPLOYEE_DDL

    def setUp(self):
        super().setUp()
        from django.contrib.auth.models import User

        self.prepare_employee_fixtures()
        member_a = User.objects.create_user(
            username="membera", first_name="Member", last_name="One"
        )
        member_b = User.objects.create_user(
            username="memberb", first_name="Beta", last_name="Member"
        )
        # Non-member manager (staff session) — only pending requests.
        insert_employee_profile(
            pk=1, employee_id="100001", user_id=None,
            role="company_manager", status="active",
        )
        # Members of shop_a / shop_b whose codes complete actions.
        insert_employee_profile(
            pk=2, employee_id="654321", user_id=member_a.pk,
            role="shop_attendant", status="active",
        )
        assign_employee_shop(employee_pk=2, shop_id=self.shop_a.pk)
        insert_employee_profile(
            pk=3, employee_id="654322", user_id=member_b.pk,
            role="shop_attendant", status="active",
        )
        assign_employee_shop(employee_pk=3, shop_id=self.shop_b.pk)
        self.item = Item.objects.create(
            category="Grocery", name="Sugar 1kg",
            minimum_selling_price=Decimal("100"),
            shop_price=Decimal("150"), stock=Decimal("40"),
            low_stock_notify=False, low_stock_threshold=0,
            is_suspended=False,
        )
        self.stock = ShopStock.objects.create(
            shop_id=self.shop_a.pk, item_id=self.item.pk,
            quantity=Decimal("10"), low_stock_threshold=5,
            average_cost=Decimal("80"),
        )
        self.login()

    def feed(self, activity):
        return Conversation.objects.get(
            kind=ConversationKind.GROUP,
            pair_key="activity:" + activity,
        )

    def post_stockin(self, **over):
        data = {
            "supplier_name": "ACME Traders",
            "supplier_phone": "0712 345 678",
            "staff_code": "654321",
            "lines": [
                {"item_id": self.item.pk, "quantity": "2", "serials": []}
            ],
        }
        data.update(over)
        return self.client.post(
            reverse("chat:api_stock_in"),
            data=json.dumps(data),
            content_type="application/json",
        )

    def test_stockin_posts_to_incoming_feed(self):
        resp = self.post_stockin()
        self.assertTrue(resp.json()["ok"])
        convo = self.feed("stockin")
        self.assertEqual(convo.name, "Incoming stock")
        # Every active shop rides along — the feed is the shared log.
        self.assertEqual(
            {p.shop_id for p in convo.participants.all()},
            {self.shop_a.pk, self.shop_b.pk},
        )
        msg = convo.messages.get()
        self.assertEqual(msg.sender_shop_id, self.shop_a.pk)
        self.assertIn("stock_in", msg.meta)
        self.assertEqual(msg.meta["by"], "Member One")
        self.assertIn("Stock received", msg.body)
        # The shop's own Assistant thread still gets its private card.
        bot = services.get_or_create_bot(self.shop_a)
        self.assertEqual(bot.messages.count(), 2)  # welcome + card

    def test_transfer_feed_card_and_resolution(self):
        resp = self.client.post(
            reverse("chat:api_transfers"),
            data=json.dumps(
                {
                    "destination_shop_id": self.shop_b.pk,
                    "staff_code": "654321",
                    "lines": [
                        {
                            "item_id": self.item.pk,
                            "quantity": "3",
                            "serials": [],
                        }
                    ],
                }
            ),
            content_type="application/json",
        )
        self.assertTrue(resp.json()["ok"])
        convo = self.feed("transfer")
        msg = convo.messages.get()
        self.assertEqual(msg.sender_shop_id, self.shop_a.pk)
        transfer = msg.meta["transfer"]
        self.assertTrue(transfer["readonly"])
        self.assertEqual(transfer["status"], "pending")
        self.assertEqual(msg.meta["by"], "Member One")
        movement_id = transfer["movement_id"]
        self.assertEqual(msg.client_tag, f"transfer-feed:{movement_id}")

        # The receiving shop accepts — the feed card resolves in place
        # and a note lands in the same group.
        client_b = Client()
        client_b.post(
            reverse("chat:login"),
            {"login_code": "222222", "password": "pass-b"},
        )
        line = StockMovementLine.objects.get(movement_id=movement_id)
        resp = client_b.post(
            reverse("chat:api_transfer_respond", args=[movement_id]),
            data=json.dumps(
                {
                    "decision": "accept",
                    "received": {str(line.pk): "3"},
                    "staff_code": "654322",
                }
            ),
            content_type="application/json",
        )
        self.assertTrue(resp.json()["ok"])
        msg.refresh_from_db()
        transfer = msg.meta["transfer"]
        self.assertEqual(transfer["status"], "fulfilled")
        self.assertEqual(transfer["responded_by"], "Beta Member")
        note = convo.messages.order_by("-id").first()
        self.assertNotEqual(note.pk, msg.pk)
        self.assertEqual(note.sender_shop_id, self.shop_b.pk)
        self.assertIn("confirmed by Beta Member", note.body)

    def test_trade_posts_to_trade_feed(self):
        resp = self.client.post(
            reverse("chat:api_sale"),
            data=json.dumps(
                {
                    "kind": "trade_out",
                    "client_name": "Joe",
                    "client_phone": "0712 345 678",
                    "staff_code": "654321",
                    "lines": [
                        {
                            "item_id": self.item.pk,
                            "quantity": "2",
                            "serials": [],
                        }
                    ],
                }
            ),
            content_type="application/json",
        )
        self.assertTrue(resp.json()["ok"])
        msg = self.feed("trade").messages.get()
        self.assertEqual(msg.sender_shop_id, self.shop_a.pk)
        self.assertEqual(msg.meta["sale"]["kind"], "trade_out")
        self.assertEqual(msg.meta["by"], "Member One")

    def test_expense_posts_to_expense_feed(self):
        resp = self.client.post(
            reverse("chat:api_expense"),
            data=json.dumps(
                {
                    "category": "transport",
                    "name": "Fuel",
                    "amount": "500",
                    "payment_status": "paid",
                    "supplier_name": "Shell Station",
                    "supplier_phone": "0712 345 678",
                    "staff_code": "654321",
                }
            ),
            content_type="application/json",
        )
        self.assertTrue(resp.json()["ok"])
        convo = self.feed("expense")
        msg = convo.messages.get()
        self.assertIn("expense", msg.meta)
        self.assertEqual(msg.meta["by"], "Member One")
        self.assertIn("FUEL", msg.body)

    def test_pending_confirm_posts_to_feed(self):
        """A request confirmed by a member still lands in the shared
        feed — named for the member who confirmed it."""
        session = self.client.session
        session["chatbot_employee_id"] = 1
        session.save()
        resp = self.post_stockin(staff_code="")
        self.assertTrue(resp.json()["pending"])
        msg = Message.objects.order_by("-id").first()
        resp = self.client.post(
            reverse("chat:api_pending_confirm", args=[msg.pk]),
            data=json.dumps({"staff_code": "654321"}),
            content_type="application/json",
        )
        self.assertTrue(resp.json()["ok"])
        feed_msg = self.feed("stockin").messages.get()
        self.assertIn("stock_in", feed_msg.meta)
        self.assertEqual(feed_msg.meta["by"], "Member One")
        self.assertEqual(feed_msg.sender_shop_id, self.shop_a.pk)

    def test_feed_group_is_separate_from_user_groups(self):
        # A user group may share the name — the feed is keyed, not named.
        user_group = services.create_group(
            self.shop_a, "Expenses", [self.shop_b]
        )
        self.client.post(
            reverse("chat:api_expense"),
            data=json.dumps(
                {
                    "category": "transport",
                    "name": "Fuel",
                    "amount": "500",
                    "payment_status": "paid",
                    "supplier_name": "Shell Station",
                    "supplier_phone": "0712 345 678",
                    "staff_code": "654321",
                }
            ),
            content_type="application/json",
        )
        feed = self.feed("expense")
        self.assertNotEqual(feed.pk, user_group.pk)
        self.assertEqual(user_group.messages.count(), 1)  # system msg only

class ReceiptsPageTests(ChatTestCase):
    """Receipts page: pending sales, list filters, confirm / cancel / return."""

    EMPLOYEE_DDL = PendingConfirmTests.EMPLOYEE_DDL

    def setUp(self):
        super().setUp()
        from django.contrib.auth.models import User

        self.prepare_employee_fixtures()
        member_user = User.objects.create_user(
            username="rcpt_member", first_name="Member", last_name="One"
        )
        insert_employee_profile(
            pk=2, employee_id="654321", user_id=member_user.pk,
            role="shop_attendant", status="active",
        )
        assign_employee_shop(employee_pk=2, shop_id=self.shop_a.pk)
        self.item = Item.objects.create(
            category="Grocery", name="Sugar 1kg",
            minimum_selling_price=Decimal("100"),
            shop_price=Decimal("150"), stock=Decimal("40"),
            low_stock_notify=False, low_stock_threshold=0,
            is_suspended=False,
        )
        self.stock = ShopStock.objects.create(
            shop_id=self.shop_a.pk, item_id=self.item.pk,
            quantity=Decimal("10"), low_stock_threshold=5,
            average_cost=Decimal("80"),
        )
        self.login()

    def make_sale(self, qty="2"):
        resp = self.client.post(
            reverse("chat:api_sale"),
            data=json.dumps({
                "kind": "sale",
                "payment_method": "cash",
                "staff_code": "654321",
                "lines": [
                    {"item_id": self.item.pk, "quantity": qty, "serials": []}
                ],
            }),
            content_type="application/json",
        )
        data = resp.json()
        self.assertTrue(data["ok"], data)
        return ShopReceipt.objects.order_by("-id").first()

    def test_page_requires_login(self):
        self.client.logout()
        resp = self.client.get(reverse("chat:receipts"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse("chat:login"), resp["Location"])

    def test_page_renders_when_logged_in(self):
        resp = self.client.get(reverse("chat:receipts"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "data-rcpt-app")
        self.assertContains(resp, "Receipts")

    def test_sale_is_pending_and_confirmable(self):
        receipt = self.make_sale()
        self.assertEqual(receipt.status, "active")
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal("8"))

        resp = self.client.post(
            reverse("chat:api_receipts_confirm"),
            data=json.dumps({"ids": [receipt.pk], "staff_code": "654321"}),
            content_type="application/json",
        )
        data = resp.json()
        self.assertTrue(data["ok"], data)
        receipt.refresh_from_db()
        self.assertEqual(receipt.status, "confirmed")

    def test_confirm_rejects_non_pending(self):
        receipt = self.make_sale()
        receipt.status = "confirmed"
        receipt.save(update_fields=["status"])
        resp = self.client.post(
            reverse("chat:api_receipts_confirm"),
            data=json.dumps({"ids": [receipt.pk], "staff_code": "654321"}),
            content_type="application/json",
        )
        self.assertFalse(resp.json()["ok"])

    def test_cancel_pending_restocks(self):
        receipt = self.make_sale("3")
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal("7"))
        resp = self.client.post(
            reverse("chat:api_receipts_cancel"),
            data=json.dumps({"ids": [receipt.pk], "staff_code": "654321"}),
            content_type="application/json",
        )
        data = resp.json()
        self.assertTrue(data["ok"], data)
        receipt.refresh_from_db()
        self.assertEqual(receipt.status, "cancelled")
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal("10"))

    def test_partial_return(self):
        receipt = self.make_sale("4")
        line = ShopReceiptLine.objects.get(receipt_id=receipt.pk)
        resp = self.client.post(
            reverse("chat:api_receipts_return"),
            data=json.dumps({
                "staff_code": "654321",
                "reason": "Damaged item",
                "receipts": [{
                    "id": receipt.pk,
                    "lines": [{"line_id": line.pk, "quantity": "1"}],
                }],
            }),
            content_type="application/json",
        )
        data = resp.json()
        self.assertTrue(data["ok"], data)
        receipt.refresh_from_db()
        self.assertEqual(receipt.status, "partial_return")
        line.refresh_from_db()
        self.assertEqual(line.returned_quantity, Decimal("1.000"))
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal("7"))  # sold 4, returned 1

    def test_list_filters_by_status_and_day(self):
        receipt = self.make_sale()
        day = timezone.localdate().isoformat()
        resp = self.client.get(
            reverse("chat:api_receipts_list"),
            {"mode": "day", "day": day, "status": "pending", "kind": "sale"},
        )
        data = resp.json()
        self.assertTrue(data["ok"])
        ids = [r["id"] for r in data["receipts"]]
        self.assertIn(receipt.pk, ids)
        self.assertTrue(
            all(r["status"] == "active" for r in data["receipts"])
        )

        resp = self.client.get(
            reverse("chat:api_receipts_list"),
            {"mode": "day", "day": day, "status": "confirmed", "kind": "sale"},
        )
        ids = [r["id"] for r in resp.json()["receipts"]]
        self.assertNotIn(receipt.pk, ids)

    def test_detail_endpoint(self):
        receipt = self.make_sale()
        resp = self.client.get(
            reverse("chat:api_receipt_detail", args=[receipt.pk])
        )
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["receipt"]["receipt_number"], receipt.receipt_number)
        self.assertTrue(data["receipt"]["lines"])
        self.assertTrue(data["receipt"]["can_confirm"])

    def test_app_has_receipts_fab(self):
        resp = self.client.get(reverse("chat:app"))
        self.assertContains(resp, 'data-fab="receipts"')
        self.assertContains(resp, "data-lucide=\"receipt\"")
        self.assertContains(resp, 'data-fab="stockin"')
        self.assertContains(resp, 'data-fab="expense"')
        self.assertContains(resp, "data-fab-others-toggle")


class StoreManagerStockTests(ChatTestCase):
    """Central store stock in/out/report — separate from shop floor stock."""

    EMPLOYEE_DDL = (
        "CREATE TABLE IF NOT EXISTS employees_employeeprofile ("
        "id INTEGER PRIMARY KEY, employee_id VARCHAR(12) NOT NULL, "
        "user_id BIGINT, role VARCHAR(32) NOT NULL, "
        "status VARCHAR(16) NOT NULL)",
        "CREATE TABLE IF NOT EXISTS "
        "employees_employeeprofile_assigned_shops ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "employeeprofile_id BIGINT NOT NULL, shop_id BIGINT NOT NULL)",
    )

    def setUp(self):
        super().setUp()
        from django.contrib.auth.models import User

        self.prepare_employee_fixtures()

        self.manager_user = User.objects.create_user(
            username="storemgr", password="pass-s",
            first_name="Sam", last_name="Store",
        )
        self.helper_user = User.objects.create_user(
            username="helper1", password="pass-h",
            first_name="Helen", last_name="Helper",
        )
        self.cashier_user = User.objects.create_user(
            username="cashierx", password="pass-c",
            first_name="Chris", last_name="Cash",
        )
        insert_employee_profile(
            pk=20, employee_id="300001", user_id=self.manager_user.pk,
            role="store_manager", status="active",
        )
        insert_employee_profile(
            pk=21, employee_id="300002", user_id=self.helper_user.pk,
            role="employee", status="active",
        )
        insert_employee_profile(
            pk=22, employee_id="300003", user_id=self.cashier_user.pk,
            role="shop_cashier", status="active",
        )

        self.item = Item.objects.create(
            category="Grocery", name="Sugar 1kg",
            minimum_selling_price=Decimal("100"),
            shop_price=Decimal("150"), stock=Decimal("40"),
            low_stock_notify=False, low_stock_threshold=0,
            is_suspended=False,
        )
        self.shop_stock = ShopStock.objects.create(
            shop_id=self.shop_a.pk, item_id=self.item.pk,
            quantity=Decimal("10"), low_stock_threshold=5,
            average_cost=Decimal("80"),
        )

    def login_store_manager(self):
        return self.client.post(
            reverse("chat:login"),
            {"mode": "employee", "login_code": "300001", "password": "pass-s"},
        )

    def test_store_manager_home_has_tools(self):
        resp = self.login_store_manager()
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            resp["Location"], reverse("chat:role_home", args=["store-manager"])
        )
        page = self.client.get(resp["Location"])
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Dashboard")
        self.assertContains(page, "Store Stock In")
        self.assertContains(page, "Store Assistant")
        self.assertContains(page, "Store Report")
        self.assertContains(page, "data-sm-feed")
        self.assertContains(page, 'data-sm-open="feed"')
        self.assertContains(page, 'data-sm-open="in"')
        self.assertContains(page, 'data-sm-open="out"')
        self.assertContains(page, 'data-sm-open="report"')
        self.assertContains(page, "Type item name")
        self.assertContains(page, 'data-sm-add')
        self.assertContains(page, "wa-app__contacts-head")
        self.assertContains(page, "wa-stock__foot")
        self.assertContains(page, "store-manager.js")

    def test_cashier_cannot_use_store_apis(self):
        self.client.post(
            reverse("chat:login"),
            {"mode": "employee", "login_code": "300003", "password": "pass-c"},
        )
        resp = self.client.get(reverse("chat:api_store_items"))
        self.assertEqual(resp.status_code, 403)

    def test_stock_in_out_separate_from_shop_stock(self):
        self.login_store_manager()
        in_resp = self.client.post(
            reverse("chat:api_store_stock_in"),
            data=json.dumps({
                "received_by_id": 21,
                "note": "Delivery A",
                "lines": [{"item_id": self.item.pk, "quantity": "5"}],
            }),
            content_type="application/json",
        )
        self.assertEqual(in_resp.status_code, 200, in_resp.content)
        in_data = in_resp.json()
        self.assertTrue(in_data["ok"])
        self.assertIn("store_stock", in_data["card"])
        self.assertEqual(in_data["card"]["store_stock"]["mode"], "in")
        self.assertIn("Store stock in", in_data["body"])
        store = StoreStock.objects.get(item_id=self.item.pk)
        self.assertEqual(store.quantity, Decimal("5.000"))

        self.item.refresh_from_db()
        self.shop_stock.refresh_from_db()
        self.assertEqual(self.item.stock, Decimal("40"))
        self.assertEqual(self.shop_stock.quantity, Decimal("10"))

        out_resp = self.client.post(
            reverse("chat:api_store_stock_out"),
            data=json.dumps({
                "received_by_id": 21,
                "lines": [{"item_id": self.item.pk, "quantity": "2"}],
            }),
            content_type="application/json",
        )
        self.assertEqual(out_resp.status_code, 200, out_resp.content)
        self.assertTrue(out_resp.json()["ok"])
        store.refresh_from_db()
        self.assertEqual(store.quantity, Decimal("3.000"))
        self.assertEqual(StoreStockMovement.objects.count(), 2)

        self.item.refresh_from_db()
        self.shop_stock.refresh_from_db()
        self.assertEqual(self.item.stock, Decimal("40"))
        self.assertEqual(self.shop_stock.quantity, Decimal("10"))

    def test_stock_out_rejects_overdraw(self):
        self.login_store_manager()
        StoreStock.objects.create(item_id=self.item.pk, quantity=Decimal("1"))
        resp = self.client.post(
            reverse("chat:api_store_stock_out"),
            data=json.dumps({
                "received_by_id": 21,
                "lines": [{"item_id": self.item.pk, "quantity": "3"}],
            }),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Store only has", resp.json()["error"])

    def test_store_report_lists_movements(self):
        self.login_store_manager()
        self.client.post(
            reverse("chat:api_store_stock_in"),
            data=json.dumps({
                "received_by_id": 21,
                "note": "Truck 1",
                "lines": [{"item_id": self.item.pk, "quantity": "4"}],
            }),
            content_type="application/json",
        )
        report = self.client.get(reverse("chat:api_store_report") + "?range=all")
        self.assertEqual(report.status_code, 200)
        data = report.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["row_count"], 1)
        self.assertEqual(data["total_in"], "4.000")
        self.assertEqual(data["rows"][0]["counterpart"], "Helen Helper")
        self.assertEqual(data["rows"][0]["note"], "Truck 1")

    def test_employee_search(self):
        self.login_store_manager()
        resp = self.client.get(
            reverse("chat:api_store_employees") + "?q=Helen"
        )
        self.assertEqual(resp.status_code, 200)
        people = resp.json()["employees"]
        self.assertEqual(len(people), 1)
        self.assertEqual(people[0]["id"], 21)
        self.assertEqual(people[0]["name"], "Helen Helper")

    def test_store_feed_returns_cards(self):
        self.login_store_manager()
        self.client.post(
            reverse("chat:api_store_stock_in"),
            data=json.dumps({
                "received_by_id": 21,
                "note": "Truck",
                "lines": [{"item_id": self.item.pk, "quantity": "2"}],
            }),
            content_type="application/json",
        )
        feed = self.client.get(reverse("chat:api_store_feed"))
        self.assertEqual(feed.status_code, 200)
        messages = feed.json()["messages"]
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["card"]["store_stock"]["mode"], "in")
        self.assertEqual(
            messages[0]["card"]["store_stock"]["counterpart"], "Helen Helper"
        )


class RiderRoleTests(ChatTestCase):
    """WhatsApp-style rider board: open → accept → complete."""

    EMPLOYEE_DDL = (
        "CREATE TABLE IF NOT EXISTS employees_employeeprofile ("
        "id INTEGER PRIMARY KEY, employee_id VARCHAR(12) NOT NULL, "
        "user_id BIGINT, role VARCHAR(32) NOT NULL, "
        "status VARCHAR(16) NOT NULL)",
        "CREATE TABLE IF NOT EXISTS "
        "employees_employeeprofile_assigned_shops ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "employeeprofile_id BIGINT NOT NULL, shop_id BIGINT NOT NULL)",
    )

    def setUp(self):
        super().setUp()
        from django.contrib.auth.models import User

        self.prepare_employee_fixtures()

        self.rider_user = User.objects.create_user(
            username="rider1", password="pass-r",
            first_name="Rita", last_name="Rider",
        )
        self.other_rider_user = User.objects.create_user(
            username="rider2", password="pass-r2",
            first_name="Ron", last_name="Rider",
        )
        self.cashier_user = User.objects.create_user(
            username="cashrider", password="pass-c",
            first_name="Casey", last_name="Cash",
        )
        insert_employee_profile(
            pk=30, employee_id="400001", user_id=self.rider_user.pk,
            role="rider", status="active",
        )
        insert_employee_profile(
            pk=31, employee_id="400002", user_id=self.other_rider_user.pk,
            role="rider", status="active",
        )
        insert_employee_profile(
            pk=32, employee_id="400003", user_id=self.cashier_user.pk,
            role="shop_cashier", status="active",
        )

        self.sale_receipt = self.create_receipt(
            shop_id=self.shop_a.pk,
            receipt_number="DLV-S-1",
            kind="sale",
            payment_method="cash",
            client_name="Ann Client",
            client_phone="0711000111",
            total=Decimal("2500"),
            amount_paid=Decimal("2500"),
            cash_amount=Decimal("2500"),
            mpesa_amount=Decimal("0"),
            status="confirmed",
            created_by_id=32,
        )
        self.credit_receipt = self.create_receipt(
            shop_id=self.shop_a.pk,
            receipt_number="DLV-C-1",
            kind="credit",
            payment_method="none",
            client_name="Bob Credit",
            client_phone="0722000222",
            total=Decimal("1800"),
            amount_paid=Decimal("0"),
            cash_amount=Decimal("0"),
            mpesa_amount=Decimal("0"),
            status="confirmed",
            created_by_id=32,
        )
        self.open_sale = ShopDelivery.objects.create(
            receipt_id=self.sale_receipt.pk,
            shop_id=self.shop_a.pk,
            status=ShopDelivery.STATUS_OPEN,
            location_text="Kilimani Gate B",
            maps_url="https://maps.example/kilimani",
            distance_km=Decimal("4.50"),
            eta_minutes=18,
            created_by_id=32,
        )
        self.open_credit = ShopDelivery.objects.create(
            receipt_id=self.credit_receipt.pk,
            shop_id=self.shop_a.pk,
            status=ShopDelivery.STATUS_OPEN,
            location_text="Westlands Plaza",
            distance_km=Decimal("6.00"),
            eta_minutes=25,
            created_by_id=32,
        )

    def login_rider(self, code="400001", password="pass-r"):
        return self.client.post(
            reverse("chat:login"),
            {"mode": "employee", "login_code": code, "password": password},
        )

    def test_rider_home_has_delivery_chat(self):
        resp = self.login_rider()
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            resp["Location"], reverse("chat:role_home", args=["rider"])
        )
        page = self.client.get(resp["Location"])
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Delivery Assistant")
        self.assertContains(page, "data-rider-feed")
        self.assertContains(page, "data-rider-tab=\"open\"")
        self.assertContains(page, "rider.js")

    def test_cashier_cannot_use_rider_apis(self):
        self.client.post(
            reverse("chat:login"),
            {"mode": "employee", "login_code": "400003", "password": "pass-c"},
        )
        resp = self.client.get(reverse("chat:api_rider_board"))
        self.assertEqual(resp.status_code, 403)

    def test_board_hides_client_on_open(self):
        self.login_rider()
        resp = self.client.get(reverse("chat:api_rider_board"))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["open_count"], 2)
        open_job = next(j for j in data["open"] if j["id"] == self.open_sale.pk)
        self.assertEqual(open_job["location_text"], "Kilimani Gate B")
        self.assertEqual(open_job["client_name"], "")
        self.assertEqual(open_job["client_phone"], "")
        self.assertTrue(open_job["client_hidden"])

    def test_accept_then_complete_sale(self):
        self.login_rider()
        accept = self.client.post(
            reverse("chat:api_rider_accept", args=[self.open_sale.pk]),
            data=json.dumps({}),
            content_type="application/json",
        )
        self.assertEqual(accept.status_code, 200, accept.content)
        card = accept.json()["delivery"]
        self.assertEqual(card["status"], "accepted")
        self.assertEqual(card["client_name"], "Ann Client")
        self.assertEqual(card["client_phone"], "0711000111")

        self.open_sale.refresh_from_db()
        self.assertEqual(self.open_sale.status, ShopDelivery.STATUS_ACCEPTED)
        self.assertEqual(self.open_sale.accepted_by_id, 30)

        complete = self.client.post(
            reverse("chat:api_rider_complete", args=[self.open_sale.pk]),
            data=json.dumps({"receiver_name": "Ann Client"}),
            content_type="application/json",
        )
        self.assertEqual(complete.status_code, 200, complete.content)
        done = complete.json()["delivery"]
        self.assertEqual(done["status"], "delivered")
        self.assertEqual(done["receiver_name"], "Ann Client")

        self.open_sale.refresh_from_db()
        self.assertEqual(self.open_sale.status, ShopDelivery.STATUS_DELIVERED)
        self.sale_receipt.refresh_from_db()
        self.assertEqual(self.sale_receipt.status, "confirmed")
        self.assertEqual(self.sale_receipt.kind, "sale")

    def test_second_rider_cannot_accept_claimed_job(self):
        self.login_rider()
        first = self.client.post(
            reverse("chat:api_rider_accept", args=[self.open_sale.pk]),
            data=json.dumps({}),
            content_type="application/json",
        )
        self.assertEqual(first.status_code, 200)

        self.client.post(reverse("chat:logout"))
        self.login_rider("400002", "pass-r2")
        second = self.client.post(
            reverse("chat:api_rider_accept", args=[self.open_sale.pk]),
            data=json.dumps({}),
            content_type="application/json",
        )
        self.assertEqual(second.status_code, 400)
        self.assertIn("already accepted", second.json()["error"])

    def test_assigned_rider_only(self):
        ShopDeliveryAssignedRider.objects.create(
            shopdelivery_id=self.open_sale.pk,
            employeeprofile_id=31,
        )
        self.login_rider()  # rider 30
        denied = self.client.post(
            reverse("chat:api_rider_accept", args=[self.open_sale.pk]),
            data=json.dumps({}),
            content_type="application/json",
        )
        self.assertEqual(denied.status_code, 400)
        self.assertIn("not assigned", denied.json()["error"])

        board = self.client.get(reverse("chat:api_rider_board")).json()
        open_ids = {row["id"] for row in board["open"]}
        self.assertNotIn(self.open_sale.pk, open_ids)
        self.assertIn(self.open_credit.pk, open_ids)

    def test_credit_complete_unpaid_and_paid(self):
        self.login_rider()
        self.client.post(
            reverse("chat:api_rider_accept", args=[self.open_credit.pk]),
            data=json.dumps({}),
            content_type="application/json",
        )
        unpaid = self.client.post(
            reverse("chat:api_rider_complete", args=[self.open_credit.pk]),
            data=json.dumps({}),
            content_type="application/json",
        )
        self.assertEqual(unpaid.status_code, 200, unpaid.content)
        self.assertEqual(unpaid.json()["delivery"]["status"], "delivered")
        self.open_credit.refresh_from_db()
        self.assertEqual(self.open_credit.status, ShopDelivery.STATUS_DELIVERED)
        self.assertEqual(self.open_credit.payment_reference, "")

        # Re-open a fresh credit job for paid path.
        credit2 = self.create_receipt(
            shop_id=self.shop_a.pk,
            receipt_number="DLV-C-2",
            kind="credit",
            payment_method="none",
            client_name="Bob Credit",
            client_phone="0722000222",
            total=Decimal("900"),
            amount_paid=Decimal("0"),
            cash_amount=Decimal("0"),
            mpesa_amount=Decimal("0"),
            status="confirmed",
            created_by_id=32,
        )
        job2 = ShopDelivery.objects.create(
            receipt_id=credit2.pk,
            shop_id=self.shop_a.pk,
            status=ShopDelivery.STATUS_OPEN,
            location_text="Parklands",
            created_by_id=32,
        )
        self.client.post(
            reverse("chat:api_rider_accept", args=[job2.pk]),
            data=json.dumps({}),
            content_type="application/json",
        )
        paid = self.client.post(
            reverse("chat:api_rider_complete", args=[job2.pk]),
            data=json.dumps({
                "payment_reference": "QK12ABCDE",
                "payment_amount": "900",
            }),
            content_type="application/json",
        )
        self.assertEqual(paid.status_code, 200, paid.content)
        self.assertEqual(paid.json()["delivery"]["status"], "delivered_paid")
        job2.refresh_from_db()
        self.assertEqual(job2.status, ShopDelivery.STATUS_DELIVERED_PAID)
        self.assertEqual(job2.payment_reference, "QK12ABCDE")
        credit2.refresh_from_db()
        self.assertEqual(credit2.kind, "credit")
        self.assertEqual(credit2.amount_paid, Decimal("0"))
