-- 014: Канонические роли и права учётных записей.
--
-- Миграция не применяется автоматически. Её запускает только техническая
-- учётная запись через runner после 001--013, включая состояние 010 и 011.
-- Это forward-only миграция MySQL 8 / InnoDB / utf8mb4; legacy-код и его
-- хранилища она не читает и не изменяет.
--
-- role_code сохраняется NOT NULL уникальным compatibility alias для legacy
-- запросов и пользователей. Он больше не является канонической ролью: роль
-- учётной записи задаётся ссылкой role_id на tn_roles.

-- 1. Канонические роли отделены от конкретных учётных записей.
CREATE TABLE IF NOT EXISTS tn_roles (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    code VARCHAR(20) NOT NULL,
    label VARCHAR(80) NOT NULL,
    active TINYINT(1) NOT NULL DEFAULT 1,
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_tn_roles_code (code),
    CONSTRAINT chk_tn_roles_code
        CHECK (code IN ('admin', 'dispatcher', 'accountant', 'operator'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

INSERT INTO tn_roles (code, label, active, created_at, updated_at) VALUES
    ('admin', 'Администратор', 1, UTC_TIMESTAMP(6), UTC_TIMESTAMP(6)),
    ('dispatcher', 'Диспетчер', 1, UTC_TIMESTAMP(6), UTC_TIMESTAMP(6)),
    ('accountant', 'Бухгалтер', 1, UTC_TIMESTAMP(6), UTC_TIMESTAMP(6)),
    ('operator', 'Оператор', 1, UTC_TIMESTAMP(6), UTC_TIMESTAMP(6))
ON DUPLICATE KEY UPDATE code = code;

-- 2. Снимаем временную статическую проверку legacy alias и сохраняем его
-- уникальность под точным именем, отражающим compatibility-семантику.
ALTER TABLE tn_operator_accounts
    DROP CHECK chk_tn_operator_accounts_role,
    DROP INDEX uq_tn_operator_accounts_role,
    ADD UNIQUE KEY uq_tn_operator_accounts_legacy_code (role_code),
    ADD COLUMN role_id BIGINT UNSIGNED NULL AFTER role_code,
    ADD KEY idx_tn_operator_accounts_role_id (role_id);

-- 3. Каждая существующая учётная запись получает одну каноническую роль.
UPDATE tn_operator_accounts AS account
JOIN tn_roles AS role
    ON role.code = CASE account.role_code
        WHEN 'admin' THEN 'admin'
        WHEN 'dispatcher' THEN 'dispatcher'
        WHEN 'accountant1' THEN 'accountant'
        WHEN 'accountant2' THEN 'accountant'
        WHEN 'accountant3' THEN 'accountant'
        WHEN 'operator1' THEN 'operator'
        WHEN 'operator2' THEN 'operator'
        WHEN 'operator3' THEN 'operator'
    END
SET account.role_id = role.id
WHERE account.role_code IN (
    'admin', 'dispatcher',
    'accountant1', 'accountant2', 'accountant3',
    'operator1', 'operator2', 'operator3'
);

ALTER TABLE tn_operator_accounts
    MODIFY COLUMN role_id BIGINT UNSIGNED NOT NULL AFTER role_code,
    ADD CONSTRAINT fk_tn_operator_accounts_role
        FOREIGN KEY (role_id) REFERENCES tn_roles(id) ON DELETE RESTRICT;

-- 4. Матрица базовых прав роли и точечные nullable overrides учётки.
CREATE TABLE IF NOT EXISTS tn_role_permissions (
    role_id BIGINT UNSIGNED NOT NULL,
    capability VARCHAR(80) NOT NULL,
    can_view TINYINT(1) NOT NULL DEFAULT 0,
    can_edit TINYINT(1) NOT NULL DEFAULT 0,
    can_print TINYINT(1) NOT NULL DEFAULT 0,
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    PRIMARY KEY (role_id, capability),
    KEY idx_tn_role_permissions_capability (capability),
    CONSTRAINT fk_tn_role_permissions_role
        FOREIGN KEY (role_id) REFERENCES tn_roles(id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS tn_account_permissions (
    account_id BIGINT UNSIGNED NOT NULL,
    capability VARCHAR(80) NOT NULL,
    can_view TINYINT(1) NULL,
    can_edit TINYINT(1) NULL,
    can_print TINYINT(1) NULL,
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    PRIMARY KEY (account_id, capability),
    CONSTRAINT fk_tn_account_permissions_account
        FOREIGN KEY (account_id) REFERENCES tn_operator_accounts(id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 5. Единственная подтверждённая capability нового контура. Повторный запуск
-- не меняет уже выданные или позднее изменённые права.
INSERT INTO tn_role_permissions (
    role_id,
    capability,
    can_view,
    can_edit,
    can_print,
    created_at,
    updated_at
)
SELECT role.id, 'trip.create', 1, 1, 0, UTC_TIMESTAMP(6), UTC_TIMESTAMP(6)
FROM tn_roles AS role
WHERE role.code IN ('admin', 'dispatcher')
ON DUPLICATE KEY UPDATE role_id = role_id;

-- Эта стадия не создаёт триггеры и не хранит результаты повторных команд.
-- Репозиторий рейсов остаётся NotImplemented, а router рейсов не регистрируется
-- до миграции 016, которая добавит долговечную защиту повторов.
