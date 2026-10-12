-- 010: Учётные записи четырёх ролей и справочники администратора.
-- Новая модель: одна программа, документы и физика в одной базе.
-- Старая SQLite-Таксимо не читается и не изменяется.
--
-- Миграция не применяется автоматически: запуск только технической учётной
-- записью через scripts/migrate-taksimo-new-mysql.sh.

-- 1. Роли: админ, диспетчер, бухгалтеры и операторы в одной таблице учёток.

ALTER TABLE tn_operator_accounts
    DROP CHECK chk_tn_operator_accounts_role;

ALTER TABLE tn_operator_accounts
    ADD COLUMN login VARCHAR(80) NULL AFTER role_code,
    ADD CONSTRAINT chk_tn_operator_accounts_role
        CHECK (role_code IN (
            'admin',
            'dispatcher',
            'accountant1', 'accountant2', 'accountant3',
            'operator1', 'operator2', 'operator3'
        )),
    ADD UNIQUE KEY uq_tn_operator_accounts_login (login);

-- 2. Типы блоков: буква плиты, её вес и стоимость.
-- Вес подтверждён владельцем 12.10.2026. Справочник задаёт значения по
-- умолчанию; строки документов сохраняют собственный снимок веса и стоимости.

CREATE TABLE IF NOT EXISTS tn_block_types (
    letter CHAR(1) NOT NULL,
    weight_kg INT UNSIGNED NOT NULL,
    price_rub DECIMAL(12, 2) NOT NULL DEFAULT 0.00,
    sort_order TINYINT UNSIGNED NOT NULL DEFAULT 0,
    updated_at DATETIME(6) NOT NULL,
    PRIMARY KEY (letter),
    CONSTRAINT chk_tn_block_types_letter CHECK (letter IN ('A', 'B', 'C', 'D', 'E', 'F', 'K')),
    CONSTRAINT chk_tn_block_types_weight CHECK (weight_kg > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

INSERT INTO tn_block_types (letter, weight_kg, price_rub, sort_order, updated_at) VALUES
    ('A', 7780, 0.00, 1, UTC_TIMESTAMP(6)),
    ('B', 7900, 0.00, 2, UTC_TIMESTAMP(6)),
    ('C', 8080, 0.00, 3, UTC_TIMESTAMP(6)),
    ('D', 8080, 0.00, 4, UTC_TIMESTAMP(6)),
    ('E', 7900, 0.00, 5, UTC_TIMESTAMP(6)),
    ('F', 7780, 0.00, 6, UTC_TIMESTAMP(6)),
    ('K', 3850, 0.00, 7, UTC_TIMESTAMP(6))
ON DUPLICATE KEY UPDATE letter = letter;

-- 3. Суффиксы состояния блока (доменное правило старой Таксимо).

CREATE TABLE IF NOT EXISTS tn_block_suffixes (
    code VARCHAR(8) NOT NULL,
    label VARCHAR(40) NOT NULL,
    requires_defect TINYINT(1) NOT NULL DEFAULT 0,
    sort_order TINYINT UNSIGNED NOT NULL DEFAULT 0,
    PRIMARY KEY (code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

INSERT INTO tn_block_suffixes (code, label, requires_defect, sort_order) VALUES
    ('',      'Без отметки',  0, 1),
    ('к',     'Короткая',     0, 2),
    ('а',     'Аварийная',    1, 3),
    ('тк',    'Трещина',      1, 4),
    ('скол',  'Скол',         1, 5)
ON DUPLICATE KEY UPDATE code = code;

-- 4. Схемы загрузки вагона. Схема присваивается перед постановкой порожнего
-- вагона в тупик; от неё зависит максимальное число плит.

CREATE TABLE IF NOT EXISTS tn_wagon_schemes (
    code VARCHAR(20) NOT NULL,
    label VARCHAR(60) NOT NULL,
    max_blocks TINYINT UNSIGNED NOT NULL,
    allowed_letters VARCHAR(40) NOT NULL DEFAULT '',
    sort_order TINYINT UNSIGNED NOT NULL DEFAULT 0,
    updated_at DATETIME(6) NOT NULL,
    PRIMARY KEY (code),
    CONSTRAINT chk_tn_wagon_schemes_max CHECK (max_blocks BETWEEN 1 AND 32)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

INSERT INTO tn_wagon_schemes (code, label, max_blocks, allowed_letters, sort_order, updated_at) VALUES
    ('scheme1', 'Схема 1', 9,  'ABCDEF', 1, UTC_TIMESTAMP(6)),
    ('scheme2', 'Схема 2', 16, 'K',      2, UTC_TIMESTAMP(6)),
    ('scheme3', 'Схема 3', 8,  'ABCDEF', 3, UTC_TIMESTAMP(6))
ON DUPLICATE KEY UPDATE code = code;

-- 5. Перевозчики: ООО или ИП, добавляется один раз.

CREATE TABLE IF NOT EXISTS tn_carriers (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    legal_form VARCHAR(8) NOT NULL,
    name VARCHAR(200) NOT NULL,
    inn VARCHAR(12) NOT NULL,
    active TINYINT(1) NOT NULL DEFAULT 1,
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_tn_carriers_inn (inn),
    CONSTRAINT chk_tn_carriers_legal_form CHECK (legal_form IN ('ООО', 'ИП')),
    CONSTRAINT chk_tn_carriers_inn CHECK (inn REGEXP '^[0-9]{10}$' OR inn REGEXP '^[0-9]{12}$')
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 6. Водители: ходят на разных машинах, могут меняться между рейсами.

CREATE TABLE IF NOT EXISTS tn_drivers (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    full_name VARCHAR(160) NOT NULL,
    phone VARCHAR(32) NOT NULL DEFAULT '',
    license_number VARCHAR(40) NOT NULL DEFAULT '',
    active TINYINT(1) NOT NULL DEFAULT 1,
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    KEY idx_tn_drivers_name (full_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 7. Машины: справочная сущность, гос.номер — идентификатор. История = рейсы.
-- Перевозчик у машины — значение по умолчанию; рейс хранит собственный снимок.

CREATE TABLE IF NOT EXISTS tn_vehicles (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    plate VARCHAR(32) NOT NULL,
    plate_tail VARCHAR(8) NOT NULL DEFAULT '',
    carrier_id BIGINT UNSIGNED NULL,
    default_driver_id BIGINT UNSIGNED NULL,
    note VARCHAR(200) NOT NULL DEFAULT '',
    active TINYINT(1) NOT NULL DEFAULT 1,
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_tn_vehicles_plate (plate),
    KEY idx_tn_vehicles_tail (plate_tail),
    KEY idx_tn_vehicles_carrier (carrier_id),
    CONSTRAINT fk_tn_vehicles_carrier FOREIGN KEY (carrier_id)
        REFERENCES tn_carriers(id) ON DELETE RESTRICT,
    CONSTRAINT fk_tn_vehicles_default_driver FOREIGN KEY (default_driver_id)
        REFERENCES tn_drivers(id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 8. Вагоны: парк около 60, добавляет администратор. Схема и текущий тупик.

CREATE TABLE IF NOT EXISTS tn_wagon_fleet (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    wagon_number VARCHAR(40) NOT NULL,
    scheme_code VARCHAR(20) NULL,
    current_dead_end VARCHAR(20) NULL,
    current_slot TINYINT UNSIGNED NULL,
    note VARCHAR(200) NOT NULL DEFAULT '',
    active TINYINT(1) NOT NULL DEFAULT 1,
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_tn_wagon_fleet_number (wagon_number),
    UNIQUE KEY uq_tn_wagon_fleet_slot (current_dead_end, current_slot),
    CONSTRAINT fk_tn_wagon_fleet_scheme FOREIGN KEY (scheme_code)
        REFERENCES tn_wagon_schemes(code) ON DELETE RESTRICT,
    CONSTRAINT chk_tn_wagon_fleet_dead_end
        CHECK (current_dead_end IS NULL OR current_dead_end IN ('gruzovoy', 'turan', 'third')),
    CONSTRAINT chk_tn_wagon_fleet_slot
        CHECK (current_slot IS NULL OR current_slot BETWEEN 1 AND 10),
    CONSTRAINT chk_tn_wagon_fleet_slot_pair
        CHECK ((current_dead_end IS NULL AND current_slot IS NULL)
            OR (current_dead_end IS NOT NULL AND current_slot IS NOT NULL))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 9. Тупики: наследуются от старой Таксимо (ГРУЗОВОЙ, ТУРАН) плюс третий.

CREATE TABLE IF NOT EXISTS tn_dead_ends (
    code VARCHAR(20) NOT NULL,
    label VARCHAR(60) NOT NULL,
    legacy_label VARCHAR(40) NOT NULL DEFAULT '',
    slot_count TINYINT UNSIGNED NOT NULL DEFAULT 10,
    is_legacy TINYINT(1) NOT NULL DEFAULT 0,
    sort_order TINYINT UNSIGNED NOT NULL DEFAULT 0,
    PRIMARY KEY (code),
    CONSTRAINT chk_tn_dead_ends_slots CHECK (slot_count BETWEEN 1 AND 30)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

INSERT INTO tn_dead_ends (code, label, legacy_label, slot_count, is_legacy, sort_order) VALUES
    ('gruzovoy', 'Грузовой',  'ГРУЗОВОЙ', 10, 1, 1),
    ('turan',    'Туран',     'ТУРАН',    10, 1, 2),
    ('third',    'Третий',    '',         10, 0, 3)
ON DUPLICATE KEY UPDATE code = code;

-- 10. Складская сетка 13 x 25, максимум 4 плиты в ячейке.

CREATE TABLE IF NOT EXISTS tn_yard_slots (
    x TINYINT UNSIGNED NOT NULL,
    y TINYINT UNSIGNED NOT NULL,
    max_blocks TINYINT UNSIGNED NOT NULL DEFAULT 4,
    PRIMARY KEY (x, y),
    CONSTRAINT chk_tn_yard_slots_x CHECK (x BETWEEN 1 AND 13),
    CONSTRAINT chk_tn_yard_slots_y CHECK (y BETWEEN 1 AND 25),
    CONSTRAINT chk_tn_yard_slots_max CHECK (max_blocks BETWEEN 1 AND 4)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

INSERT IGNORE INTO tn_yard_slots (x, y, max_blocks)
SELECT xs.x, ys.y, 4
FROM (
    SELECT 1 AS x UNION ALL SELECT 2 UNION ALL SELECT 3 UNION ALL SELECT 4 UNION ALL
    SELECT 5 UNION ALL SELECT 6 UNION ALL SELECT 7 UNION ALL SELECT 8 UNION ALL
    SELECT 9 UNION ALL SELECT 10 UNION ALL SELECT 11 UNION ALL SELECT 12 UNION ALL SELECT 13
) AS xs
CROSS JOIN (
    SELECT 1 AS y UNION ALL SELECT 2 UNION ALL SELECT 3 UNION ALL SELECT 4 UNION ALL
    SELECT 5 UNION ALL SELECT 6 UNION ALL SELECT 7 UNION ALL SELECT 8 UNION ALL
    SELECT 9 UNION ALL SELECT 10 UNION ALL SELECT 11 UNION ALL SELECT 12 UNION ALL
    SELECT 13 UNION ALL SELECT 14 UNION ALL SELECT 15 UNION ALL SELECT 16 UNION ALL
    SELECT 17 UNION ALL SELECT 18 UNION ALL SELECT 19 UNION ALL SELECT 20 UNION ALL
    SELECT 21 UNION ALL SELECT 22 UNION ALL SELECT 23 UNION ALL SELECT 24 UNION ALL SELECT 25
) AS ys;

-- 11. Журнал действий: кто что сделал и кто что сохранил.

CREATE TABLE IF NOT EXISTS tn_action_log (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    account_id BIGINT UNSIGNED NULL,
    role_code VARCHAR(20) NOT NULL,
    action VARCHAR(80) NOT NULL,
    subject_type VARCHAR(40) NOT NULL DEFAULT '',
    subject_id VARCHAR(64) NOT NULL DEFAULT '',
    details_json JSON NULL,
    remote_address VARCHAR(128) NOT NULL DEFAULT '',
    created_at DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    KEY idx_tn_action_log_account (account_id, created_at),
    KEY idx_tn_action_log_action (action, created_at),
    KEY idx_tn_action_log_subject (subject_type, subject_id),
    CONSTRAINT fk_tn_action_log_account FOREIGN KEY (account_id)
        REFERENCES tn_operator_accounts(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
