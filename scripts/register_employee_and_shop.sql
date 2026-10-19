-- ============================================================================
-- MY-SHOP seed script
--
--   Employee : ID 000000 / password 000000  (Super Admin, ACTIVE)
--   Shop     : login code 123456 / password 000000  ("SHOP 1")
--
-- Paste the whole file into phpMyAdmin -> SQL tab, or run it via PyMySQL
-- with multi-statements enabled. Safe to re-run: existing rows are updated.
--
-- The password values below are real Django Argon2 hashes of "000000".
-- Edit the placeholder names / email / phone to taste before running.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1. Employee login user (auth_user)
-- ----------------------------------------------------------------------------
INSERT INTO auth_user
    (password, is_superuser, username, first_name, last_name,
     email, is_staff, is_active, date_joined, last_login)
VALUES
    ('argon2$argon2id$v=19$m=102400,t=2,p=8$VlNscmk5R3hXZnEyMk9ENFY1MWlLaw$l2pAfFcgxkZZuP1EZFw/0RDD+meKBich+8nzv0yYE0E',
     0, '000000', 'SYSTEM', 'ADMIN',
     'admin@example.com', 0, 1, NOW(6), NULL)
ON DUPLICATE KEY UPDATE
    password   = 'argon2$argon2id$v=19$m=102400,t=2,p=8$VlNscmk5R3hXZnEyMk9ENFY1MWlLaw$l2pAfFcgxkZZuP1EZFw/0RDD+meKBich+8nzv0yYE0E',
    first_name = 'SYSTEM',
    last_name  = 'ADMIN',
    email      = 'admin@example.com',
    is_active  = 1;

SET @uid := (SELECT id FROM auth_user WHERE username = '000000');

-- ----------------------------------------------------------------------------
-- 2. Employee profile (employees_employeeprofile) — Super Admin, ACTIVE
-- ----------------------------------------------------------------------------
INSERT INTO employees_employeeprofile
    (user_id, employee_id, phone_country_code, phone_number,
     profile_photo, role, status, created_at, updated_at)
VALUES
    (@uid, '000000', '+254', '0700000000',
     NULL, 'super_admin', 'active', NOW(6), NOW(6))
ON DUPLICATE KEY UPDATE
    user_id            = @uid,
    phone_country_code = '+254',
    phone_number       = '0700000000',
    role               = 'super_admin',
    status             = 'active',
    updated_at         = NOW(6);

SET @eid := (SELECT id FROM employees_employeeprofile WHERE employee_id = '000000');

-- ----------------------------------------------------------------------------
-- 3. Company singleton settings rows (the app get_or_creates these; make sure
--    they exist so the shop rows below can copy them).
-- ----------------------------------------------------------------------------
INSERT IGNORE INTO shops_companyworkinghourssettings
    (id, enabled, work_monday, work_tuesday, work_wednesday, work_thursday,
     work_friday, work_saturday, work_sunday, start_time, end_time, updated_at)
VALUES
    (1, 0, 1, 1, 1, 1, 1, 0, 0, '08:00:00', '17:00:00', NOW(6));

INSERT IGNORE INTO shops_companypossettings
    (id, enable_sale, enable_credit, enable_quotation, enable_cash_sale_checkout,
     enable_cash, enable_mpesa, enable_cash_mpesa, enable_discount,
     compulsory_print_on_sale, enable_print_bluetooth, enable_print_usb,
     enable_print_wifi, enable_tax, tax_percent, receipt_paper_width,
     receipt_format_credit, receipt_format_quotation, receipt_format_sale,
     mpesa_account_number, mpesa_business_number, mpesa_collection_type,
     mpesa_till_number, receipt_font_size, receipt_font_weight,
     enable_receipt_qr, receipt_qr_content, receipt_qr_website,
     enable_receipt_payment_methods, mpesa_phone_number, enable_trade_out,
     receipt_format_trade_out, enable_stock_tracking, enable_open_close,
     enable_client_data, require_client_phone, require_client_name,
     enable_trade_in, receipt_format_trade_in, show_client_phone_on_receipt,
     updated_at)
VALUES
    (1, 1, 1, 1, 1,
     1, 1, 1, 1,
     0, 1, 1,
     1, 0, 0.00, '80',
     'C', 'Q', 'S',
     '', '', '',
     '', 'medium', 'regular',
     0, 'website', '',
     1, '', 1,
     'T', 1, 1,
     1, 0, 0,
     1, 'TI', 1,
     NOW(6));

-- ----------------------------------------------------------------------------
-- 4. Shop (shops_shop) — login code 123456, password 000000
-- ----------------------------------------------------------------------------
INSERT INTO shops_shop
    (name, location, email, phone_number, receipt_phone_number,
     login_code, password_hash, image, is_suspended, is_hidden,
     created_by_id, created_at, updated_at)
VALUES
    ('SHOP 1', 'NAIROBI', 'shop1@example.com', '0700000000', '',
     '123456',
     'argon2$argon2id$v=19$m=102400,t=2,p=8$WFJOeDVwRnVTUEJETUR2ZERHWVZmUA$bOmIgMYznE82Rb0UIykeCN55EbmCGbKitPnFtXJZSmQ',
     NULL, 0, 0,
     @eid, NOW(6), NOW(6))
ON DUPLICATE KEY UPDATE
    name          = 'SHOP 1',
    location      = 'NAIROBI',
    email         = 'shop1@example.com',
    phone_number  = '0700000000',
    password_hash = 'argon2$argon2id$v=19$m=102400,t=2,p=8$WFJOeDVwRnVTUEJETUR2ZERHWVZmUA$bOmIgMYznE82Rb0UIykeCN55EbmCGbKitPnFtXJZSmQ',
    is_suspended  = 0,
    is_hidden     = 0,
    updated_at    = NOW(6);

SET @sid := (SELECT id FROM shops_shop WHERE login_code = '123456');

-- ----------------------------------------------------------------------------
-- 5. Shop working hours — copied from company settings (same as create_shop)
-- ----------------------------------------------------------------------------
INSERT INTO shops_shopworkinghourssettings
    (shop_id, start_time, end_time, updated_at)
SELECT @sid, c.start_time, c.end_time, NOW(6)
FROM shops_companyworkinghourssettings c
WHERE c.id = 1
  AND @sid IS NOT NULL
  AND NOT EXISTS (
      SELECT 1 FROM shops_shopworkinghourssettings s WHERE s.shop_id = @sid
  );

-- ----------------------------------------------------------------------------
-- 6. Shop POS settings — copied from company settings (same as create_shop)
-- ----------------------------------------------------------------------------
INSERT INTO shops_shoppossettings
    (shop_id, override_pos, override_receipt, enable_cash_sale_checkout,
     enable_sale, enable_credit, enable_quotation, enable_trade_out,
     enable_trade_in, enable_cash, enable_mpesa, enable_cash_mpesa,
     enable_discount, enable_stock_tracking, enable_open_close,
     enable_client_data, require_client_phone, require_client_name,
     enable_tax, tax_percent, compulsory_print_on_sale,
     enable_print_bluetooth, enable_print_usb, enable_print_wifi,
     enable_receipt_payment_methods, show_client_phone_on_receipt,
     receipt_paper_width, receipt_format_sale, receipt_format_credit,
     receipt_format_quotation, receipt_format_trade_out,
     receipt_format_trade_in, mpesa_collection_type, mpesa_business_number,
     mpesa_account_number, mpesa_till_number, mpesa_phone_number,
     receipt_font_size, receipt_font_weight, enable_receipt_qr,
     receipt_qr_content, receipt_qr_website, updated_at)
SELECT @sid, 0, 0, 1,
       c.enable_sale, c.enable_credit, c.enable_quotation, c.enable_trade_out,
       c.enable_trade_in, c.enable_cash, c.enable_mpesa, c.enable_cash_mpesa,
       c.enable_discount, c.enable_stock_tracking, c.enable_open_close,
       c.enable_client_data, c.require_client_phone, c.require_client_name,
       c.enable_tax, c.tax_percent, c.compulsory_print_on_sale,
       c.enable_print_bluetooth, c.enable_print_usb, c.enable_print_wifi,
       c.enable_receipt_payment_methods, c.show_client_phone_on_receipt,
       c.receipt_paper_width, c.receipt_format_sale, c.receipt_format_credit,
       c.receipt_format_quotation, c.receipt_format_trade_out,
       c.receipt_format_trade_in, c.mpesa_collection_type, c.mpesa_business_number,
       c.mpesa_account_number, c.mpesa_till_number, c.mpesa_phone_number,
       c.receipt_font_size, c.receipt_font_weight, c.enable_receipt_qr,
       c.receipt_qr_content, c.receipt_qr_website, NOW(6)
FROM shops_companypossettings c
WHERE c.id = 1
  AND @sid IS NOT NULL
  AND NOT EXISTS (
      SELECT 1 FROM shops_shoppossettings s WHERE s.shop_id = @sid
  );

-- ----------------------------------------------------------------------------
-- Verify
-- ----------------------------------------------------------------------------
SELECT u.id, u.username, u.first_name, u.is_active, p.role, p.status
FROM auth_user u
JOIN employees_employeeprofile p ON p.user_id = u.id
WHERE u.username = '000000';

SELECT id, name, location, login_code, is_suspended, is_hidden
FROM shops_shop
WHERE login_code = '123456';
