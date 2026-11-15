-- ============================================================================
-- wipe_sales.sql — permanently delete ALL sales data from the MY-SHOP database.
--
-- Run in phpMyAdmin (select your DB → SQL tab → paste → Go) or from the shell:
--
--     "C:\xampp\mysql\bin\mysql.exe" -u root myshop_db < wipe_sales.sql
--
-- ⚠ THIS IS IRREVERSIBLE — take a backup first:
--     "C:\xampp\mysql\bin\mysqldump.exe" -u root myshop_db > backup.sql
--
-- DELETES: receipts + lines + deliveries, sale/credit M-Pesa STK payments,
--          credit-account events, queued print jobs, legacy POS sales,
--          sale-adjacent stock movements (returns / trades), day sessions.
-- KEEPS:   shops, clients, items, suppliers, employees, expenses, stock
--          quantities, settings.
-- ============================================================================

SET FOREIGN_KEY_CHECKS = 0;
START TRANSACTION;

-- ----------------------------------------------------------------------------
-- OPTIONAL — RESTORE STOCK BEFORE DELETING.
-- Puts sold (non-returned) quantities back into items_shopstock and marks
-- sold serials available again. Uncomment this whole block to enable it.
-- Note: average_cost is NOT recalculated (use wipe_sales.py for that).
-- ----------------------------------------------------------------------------
-- UPDATE items_shopstock ss
-- JOIN (
--     SELECT rl.item_id, r.shop_id,
--            SUM(rl.quantity - rl.returned_quantity) AS qty
--     FROM shops_shopreceiptline rl
--     JOIN shops_shopreceipt r ON r.id = rl.receipt_id
--     WHERE rl.item_id IS NOT NULL
--     GROUP BY rl.item_id, r.shop_id
-- ) x ON x.item_id = ss.item_id AND x.shop_id = ss.shop_id
-- SET ss.quantity = ss.quantity + x.qty;
--
-- -- serials that left on receipts become available again
-- -- (requires MariaDB 10.6+ / MySQL 8+ for JSON_TABLE; returned serials
-- --  may be over-restored — acceptable for a full wipe)
-- UPDATE items_itemserial s
-- JOIN (
--     SELECT rl.item_id, jt.sn
--     FROM shops_shopreceiptline rl,
--          JSON_TABLE(rl.serial_numbers, '$[*]'
--                     COLUMNS (sn VARCHAR(120) PATH '$')) jt
--     WHERE rl.item_id IS NOT NULL
-- ) x ON x.item_id = s.item_id AND x.sn = s.serial_number
-- SET s.is_available = 1;
--
-- -- undo stock added by deleted stock-in movements (trades; customer
-- -- returns are already netted by the line restore above)
-- UPDATE items_shopstock ss
-- JOIN (
--     SELECT ml.item_id, m.shop_id, SUM(ml.quantity) AS qty
--     FROM items_stockmovementline ml
--     JOIN items_stockmovement m ON m.id = ml.movement_id
--     WHERE ml.item_id IS NOT NULL
--       AND m.movement_type = 'in'
--       AND m.entry_source IN ('trade_in', 'trade_exchange')
--     GROUP BY ml.item_id, m.shop_id
-- ) x ON x.item_id = ss.item_id AND x.shop_id = ss.shop_id
-- SET ss.quantity = GREATEST(0, ss.quantity - x.qty);
--
-- -- undo stock removed by deleted stock-out movements (trades/returns)
-- UPDATE items_shopstock ss
-- JOIN (
--     SELECT ml.item_id, m.shop_id, SUM(ml.quantity) AS qty
--     FROM items_stockmovementline ml
--     JOIN items_stockmovement m ON m.id = ml.movement_id
--     WHERE ml.item_id IS NOT NULL
--       AND m.movement_type = 'out'
--       AND m.entry_source IN ('customer_return', 'trade_out',
--                              'trade_in', 'trade_exchange')
--     GROUP BY ml.item_id, m.shop_id
-- ) x ON x.item_id = ss.item_id AND x.shop_id = ss.shop_id
-- SET ss.quantity = ss.quantity + x.qty;
-- ----------------------------------------------------------------------------

-- Preview counts (harmless — shows what will be deleted):
SELECT 'shops_shopreceiptline'  AS tbl, COUNT(*) AS rows_to_delete FROM shops_shopreceiptline
UNION ALL SELECT 'shops_shopdelivery',               COUNT(*) FROM shops_shopdelivery
UNION ALL SELECT 'shops_shopreceipt',                COUNT(*) FROM shops_shopreceipt
UNION ALL SELECT 'shops_mpesastkpayment',            COUNT(*) FROM shops_mpesastkpayment WHERE purpose IN ('sale','credit')
UNION ALL SELECT 'shops_clientcreditaccountevent',   COUNT(*) FROM shops_clientcreditaccountevent
UNION ALL SELECT 'shops_printagentjob',              COUNT(*) FROM shops_printagentjob
UNION ALL SELECT 'pos_saleline',                     COUNT(*) FROM pos_saleline
UNION ALL SELECT 'pos_sale',                         COUNT(*) FROM pos_sale
UNION ALL SELECT 'items_stockmovement',              COUNT(*) FROM items_stockmovement WHERE entry_source IN ('customer_return','trade_out','trade_in','trade_exchange')
UNION ALL SELECT 'shops_shopdaysession',             COUNT(*) FROM shops_shopdaysession;

-- 1. Credit-account ledger (exists only because of credit sales)
DELETE FROM shops_clientcreditaccountevent;

-- 2. M-Pesa STK payments for sales & credit payments
--    ('developer' subscription STKs are kept)
DELETE FROM shops_mpesastkpayment WHERE purpose IN ('sale', 'credit');

-- 3. Queued receipt print jobs
DELETE FROM shops_printagentjob;

-- 4. Deliveries (+ their rider-assignment rows)
DELETE FROM shops_shopdelivery_assigned_riders;
DELETE FROM shops_shopdelivery;

-- 5. Receipt lines, then receipts
DELETE FROM shops_shopreceiptline;
DELETE FROM shops_shopreceipt;

-- 6. Legacy offline-POS sales
DELETE FROM pos_saleline;
DELETE FROM pos_sale;

-- 7. Sale-adjacent stock movements (customer returns, trade out/in/exchange).
--    Stock-in/out movements you made manually are kept.
DELETE ml FROM items_stockmovementline ml
JOIN items_stockmovement m ON m.id = ml.movement_id
WHERE m.entry_source IN ('customer_return', 'trade_out', 'trade_in', 'trade_exchange');

DELETE FROM items_stockmovement
WHERE entry_source IN ('customer_return', 'trade_out', 'trade_in', 'trade_exchange');

-- 8. Shop day sessions (till open/close summaries).
--    Comment these two lines out if you want to keep them.
DELETE FROM shops_shopdaysession;

-- 9. Reset client credit follow-up dates (credit data is gone)
UPDATE shops_client
SET credit_follow_up_date = NULL,
    credit_follow_up_reminder_sent_on = NULL
WHERE credit_follow_up_date IS NOT NULL
   OR credit_follow_up_reminder_sent_on IS NOT NULL;

COMMIT;
SET FOREIGN_KEY_CHECKS = 1;
