-- approve_it_support.sql
-- Approve KIMATHI MBAE (kimathimbae1@gmail.com) and assign the IT Support role.
-- Run against the MySQL/MariaDB database (e.g. via phpMyAdmin SQL tab).

-- 1) Approve + assign role
UPDATE employees_employeeprofile ep
JOIN auth_user u ON u.id = ep.user_id
SET ep.status     = 'active',
    ep.role       = 'it_support',
    ep.updated_at = NOW(6)
WHERE u.email = 'kimathimbae1@gmail.com';

-- 2) Make sure the login itself is enabled
UPDATE auth_user
SET is_active = 1
WHERE email = 'kimathimbae1@gmail.com';

-- 3) Verify
SELECT u.id, u.username, u.email, u.is_active,
       ep.employee_id, ep.status, ep.role
FROM employees_employeeprofile ep
JOIN auth_user u ON u.id = ep.user_id
WHERE u.email = 'kimathimbae1@gmail.com';

-- ---------------------------------------------------------------------------
-- To approve BOTH accounts in one pass, replace the WHERE clause above with:
--   WHERE u.email IN ('kimathasalvatore@gmail.com', 'kimathimbae1@gmail.com');
--
-- IT Support already sees every active shop (COMPANY_WIDE_SHOP_ROLES), so no
-- shop allocation rows are required. To restrict to specific shops anyway:
--
--   INSERT IGNORE INTO employees_employeeprofile_assigned_shops
--       (employeeprofile_id, shop_id)
--   SELECT ep.id, s.id
--   FROM employees_employeeprofile ep
--   JOIN auth_user u ON u.id = ep.user_id
--   JOIN shops_shop s ON s.name = '<shop name here>'
--   WHERE u.email = 'kimathimbae1@gmail.com';
-- ---------------------------------------------------------------------------
