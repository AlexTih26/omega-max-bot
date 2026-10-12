-- 011: Единый вход по ролям — логины, администратор и служебные роли.
-- Развивает 010: заводит учётные записи четырёх ролей и наполняет логины.
-- PIN задаёт только технический владелец через scripts/taksimo-new-operator.py;
-- здесь новые записи создаются без PIN, чтобы вход по ним был закрыт до выдачи.
--
-- Миграция не применяется автоматически: запуск только технической учётной
-- записью через scripts/migrate-taksimo-new-mysql.sh.

-- 1. Логины существующих операторов: вход по логину должен быть однозначным.
UPDATE tn_operator_accounts
SET login = 'operator1', updated_at = UTC_TIMESTAMP(6)
WHERE role_code = 'operator1' AND (login IS NULL OR login = '');

UPDATE tn_operator_accounts
SET login = 'operator2', updated_at = UTC_TIMESTAMP(6)
WHERE role_code = 'operator2' AND (login IS NULL OR login = '');

UPDATE tn_operator_accounts
SET login = 'operator3', updated_at = UTC_TIMESTAMP(6)
WHERE role_code = 'operator3' AND (login IS NULL OR login = '');

-- 2. Учётные записи остальных ролей.
-- Каждая создаётся только если такой роли ещё нет: повторный запуск не
-- затрагивает уже выданные PIN и не переписывает active.
INSERT INTO tn_operator_accounts (role_code, login, display_name, pin_hash, active, created_at, updated_at)
SELECT 'admin', 'admin', 'Администратор', '', 0, UTC_TIMESTAMP(6), UTC_TIMESTAMP(6)
WHERE NOT EXISTS (SELECT 1 FROM tn_operator_accounts WHERE role_code = 'admin');

INSERT INTO tn_operator_accounts (role_code, login, display_name, pin_hash, active, created_at, updated_at)
SELECT 'dispatcher', 'dispatcher', 'Диспетчер', '', 0, UTC_TIMESTAMP(6), UTC_TIMESTAMP(6)
WHERE NOT EXISTS (SELECT 1 FROM tn_operator_accounts WHERE role_code = 'dispatcher');

INSERT INTO tn_operator_accounts (role_code, login, display_name, pin_hash, active, created_at, updated_at)
SELECT 'accountant1', 'accountant1', 'Бухгалтер 1', '', 0, UTC_TIMESTAMP(6), UTC_TIMESTAMP(6)
WHERE NOT EXISTS (SELECT 1 FROM tn_operator_accounts WHERE role_code = 'accountant1');

INSERT INTO tn_operator_accounts (role_code, login, display_name, pin_hash, active, created_at, updated_at)
SELECT 'accountant2', 'accountant2', 'Бухгалтер 2', '', 0, UTC_TIMESTAMP(6), UTC_TIMESTAMP(6)
WHERE NOT EXISTS (SELECT 1 FROM tn_operator_accounts WHERE role_code = 'accountant2');

INSERT INTO tn_operator_accounts (role_code, login, display_name, pin_hash, active, created_at, updated_at)
SELECT 'accountant3', 'accountant3', 'Бухгалтер 3', '', 0, UTC_TIMESTAMP(6), UTC_TIMESTAMP(6)
WHERE NOT EXISTS (SELECT 1 FROM tn_operator_accounts WHERE role_code = 'accountant3');

-- 3. Индексы журнала действий под разбор по роли и объекту.
ALTER TABLE tn_action_log
    ADD KEY idx_tn_action_log_role (role_code, created_at),
    ADD KEY idx_tn_action_log_subject_kind (subject_type, subject_id, created_at);
