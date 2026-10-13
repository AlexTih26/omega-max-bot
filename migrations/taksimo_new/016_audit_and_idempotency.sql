-- 016: Неизменяемый аудит и долговечная идемпотентность HTTP-команд.
--
-- Зависимости: миграции 001--015, включая tn_action_log из 010, канонические
-- роли из 014 и историю очереди из 015. Миграция source-only и forward-only:
-- приложение не применяет её автоматически. Целевая СУБД: MySQL 8, InnoDB,
-- utf8mb4. Запуск разрешён только технической учётной записью через штатный
-- runner после подтверждения состояния зависимостей.
--
-- Исторические записи tn_action_log не переписываются. Новые поля снимков
-- nullable, поэтому для прежних строк не создаются вымышленные данные.

-- 1. Результат команды отделён от integration inbox/outbox. Область ключа
-- разделена между актором и стабильным кодом операции.
CREATE TABLE tn_command_idempotency (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    actor_account_id BIGINT UNSIGNED NOT NULL,
    operation_code VARCHAR(80) NOT NULL,
    idempotency_key CHAR(36) NOT NULL,
    fingerprint_algorithm VARCHAR(40) NOT NULL,
    fingerprint_version VARCHAR(40) NOT NULL,
    request_fingerprint VARCHAR(128) NOT NULL,
    status VARCHAR(16) NOT NULL,
    initial_response_status TINYINT UNSIGNED NULL,
    response_body_json JSON NULL,
    created_at DATETIME(6) NOT NULL,
    completed_at DATETIME(6) NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_tn_command_idempotency_scope (
        actor_account_id,
        operation_code,
        idempotency_key
    ),
    KEY idx_tn_command_idempotency_status_created (status, created_at),
    CONSTRAINT chk_tn_command_idempotency_key
        CHECK (
            REGEXP_LIKE(
                idempotency_key,
                '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
                'c'
            )
        ),
    CONSTRAINT chk_tn_command_idempotency_status
        CHECK (status IN ('pending', 'completed')),
    CONSTRAINT chk_tn_command_idempotency_result
        CHECK (
            (status = 'pending'
                AND initial_response_status IS NULL
                AND response_body_json IS NULL
                AND completed_at IS NULL)
            OR (status = 'completed'
                AND initial_response_status = 201
                AND response_body_json IS NOT NULL
                AND completed_at IS NOT NULL)
        ),
    CONSTRAINT fk_tn_command_idempotency_actor
        FOREIGN KEY (actor_account_id) REFERENCES tn_operator_accounts(id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 2. Развиваем единственный журнал действий: account_id и role_code уже служат
-- снимком актора. display name, capability и ссылка на повтор добавляются без
-- backfill исторических строк.
ALTER TABLE tn_action_log
    ADD COLUMN actor_display_name VARCHAR(120) NULL AFTER role_code,
    ADD COLUMN capability_code VARCHAR(80) NULL AFTER action,
    ADD COLUMN idempotency_id BIGINT UNSIGNED NULL AFTER subject_id,
    ADD KEY idx_tn_action_log_idempotency (idempotency_id),
    ADD CONSTRAINT fk_tn_action_log_idempotency
        FOREIGN KEY (idempotency_id) REFERENCES tn_command_idempotency(id) ON DELETE RESTRICT;

-- Outbox и runtime-репозиторий намеренно остаются предметом 017 и последующей
-- подтверждённой работы после применения этой миграции в целевой БД.
