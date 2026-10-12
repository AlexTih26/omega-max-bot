-- 012: Рейс диспетчера, печать ТТН и экспедиторская расписка бухгалтера.
-- Этапы 3-5 единой модели: документы и физика в одной базе.
--
-- Миграция не применяется автоматически: запуск только технической учётной
-- записью через scripts/migrate-taksimo-new-mysql.sh.

-- 1. Рейс: то, что диспетчер принимает на площадке.
-- Рейс хранит собственный снимок водителя и перевозчика: справочники могут
-- измениться, но прошлое не переписывается.
CREATE TABLE IF NOT EXISTS tn_trips (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    public_id CHAR(36) NOT NULL,
    trip_date DATE NOT NULL,
    vehicle_id BIGINT UNSIGNED NULL,
    vehicle_plate VARCHAR(32) NOT NULL,
    carrier_id BIGINT UNSIGNED NULL,
    carrier_name VARCHAR(200) NOT NULL DEFAULT '',
    carrier_inn VARCHAR(12) NOT NULL DEFAULT '',
    carrier_legal_form VARCHAR(8) NOT NULL DEFAULT '',
    driver_id BIGINT UNSIGNED NULL,
    driver_name VARCHAR(160) NOT NULL DEFAULT '',
    driver_license VARCHAR(40) NOT NULL DEFAULT '',
    block_count TINYINT UNSIGNED NOT NULL DEFAULT 0,
    total_weight_kg INT UNSIGNED NOT NULL DEFAULT 0,
    status VARCHAR(24) NOT NULL DEFAULT 'draft',
    created_by_account_id BIGINT UNSIGNED NULL,
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_tn_trips_public_id (public_id),
    KEY idx_tn_trips_date (trip_date),
    KEY idx_tn_trips_status (status, trip_date),
    KEY idx_tn_trips_vehicle (vehicle_id),
    CONSTRAINT chk_tn_trips_status
        CHECK (status IN ('draft', 'ttn_issued', 'printed', 'er_sent', 'er_returned', 'cancelled')),
    CONSTRAINT chk_tn_trips_blocks CHECK (block_count BETWEEN 0 AND 16),
    CONSTRAINT fk_tn_trips_created_by FOREIGN KEY (created_by_account_id)
        REFERENCES tn_operator_accounts(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 2. Строки рейса: блоки с снимком веса и стоимости на момент рейса.
-- Блок — самая мелкая единица учёта: буква, номер и сквозной идентификатор.
CREATE TABLE IF NOT EXISTS tn_trip_blocks (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    trip_id BIGINT UNSIGNED NOT NULL,
    block_id BIGINT UNSIGNED NULL,
    block_letter CHAR(1) NOT NULL,
    block_number VARCHAR(40) NOT NULL,
    suffix_code VARCHAR(8) NOT NULL DEFAULT '',
    weight_kg INT UNSIGNED NOT NULL,
    price_rub DECIMAL(12, 2) NOT NULL DEFAULT 0.00,
    created_at DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    KEY idx_tn_trip_blocks_trip (trip_id),
    KEY idx_tn_trip_blocks_block (block_id),
    CONSTRAINT fk_tn_trip_blocks_trip FOREIGN KEY (trip_id)
        REFERENCES tn_trips(id) ON DELETE CASCADE,
    CONSTRAINT chk_tn_trip_blocks_letter CHECK (block_letter IN ('A', 'B', 'C', 'D', 'E', 'F', 'K')),
    CONSTRAINT chk_tn_trip_blocks_weight CHECK (weight_kg > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 3. Номер ТТН: выдаётся один раз, затем неизменяем.
-- Формат утверждённого номера: ТТН №РМ-ГГГГ-ЧЧЧЧЧЧ.
CREATE TABLE IF NOT EXISTS tn_ttn_numbers (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    year SMALLINT UNSIGNED NOT NULL,
    sequence_number INT UNSIGNED NOT NULL,
    ttn_number VARCHAR(120) NOT NULL,
    trip_id BIGINT UNSIGNED NOT NULL,
    issued_by_account_id BIGINT UNSIGNED NULL,
    issued_at DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_tn_ttn_numbers_number (ttn_number),
    UNIQUE KEY uq_tn_ttn_numbers_trip (trip_id),
    KEY idx_tn_ttn_numbers_year (year, sequence_number),
    CONSTRAINT fk_tn_ttn_numbers_trip FOREIGN KEY (trip_id)
        REFERENCES tn_trips(id) ON DELETE RESTRICT,
    CONSTRAINT fk_tn_ttn_numbers_issued_by FOREIGN KEY (issued_by_account_id)
        REFERENCES tn_operator_accounts(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 4. Печатные экземпляры ТТН: четыре обязательных копии одного рейса.
-- Содержимое неизменяемо: исправление оформляется новым рейсом и новой печатью.
CREATE TABLE IF NOT EXISTS tn_ttn_copies (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    trip_id BIGINT UNSIGNED NOT NULL,
    copy_number TINYINT UNSIGNED NOT NULL,
    filename VARCHAR(255) NOT NULL,
    content_type VARCHAR(128) NOT NULL DEFAULT '',
    content LONGBLOB NOT NULL,
    sha256 CHAR(64) NOT NULL,
    size_bytes INT UNSIGNED NOT NULL,
    downloaded_at DATETIME(6) NULL,
    downloaded_by_account_id BIGINT UNSIGNED NULL,
    created_at DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_tn_ttn_copies_trip_copy (trip_id, copy_number),
    CONSTRAINT fk_tn_ttn_copies_trip FOREIGN KEY (trip_id)
        REFERENCES tn_trips(id) ON DELETE RESTRICT,
    CONSTRAINT fk_tn_ttn_copies_downloaded_by FOREIGN KEY (downloaded_by_account_id)
        REFERENCES tn_operator_accounts(id) ON DELETE SET NULL,
    CONSTRAINT chk_tn_ttn_copies_number CHECK (copy_number BETWEEN 1 AND 4)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 5. Экспедиторская расписка: документ бухгалтера.
-- Отправку и возврат бухгалтер отмечает вручную: внешнего обмена нет,
-- в Диадок или Контур данные переносятся человеком.
CREATE TABLE IF NOT EXISTS tn_expedition_receipts (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    public_id CHAR(36) NOT NULL,
    trip_id BIGINT UNSIGNED NOT NULL,
    ttn_number VARCHAR(120) NOT NULL DEFAULT '',
    driver_name VARCHAR(160) NOT NULL DEFAULT '',
    vehicle_plate VARCHAR(32) NOT NULL DEFAULT '',
    carrier_name VARCHAR(200) NOT NULL DEFAULT '',
    carrier_inn VARCHAR(12) NOT NULL DEFAULT '',
    blocks_summary VARCHAR(500) NOT NULL DEFAULT '',
    total_weight_kg INT UNSIGNED NOT NULL DEFAULT 0,
    total_price_rub DECIMAL(14, 2) NOT NULL DEFAULT 0.00,
    status VARCHAR(24) NOT NULL DEFAULT 'draft',
    sent_at DATETIME(6) NULL,
    sent_by_account_id BIGINT UNSIGNED NULL,
    returned_at DATETIME(6) NULL,
    returned_by_account_id BIGINT UNSIGNED NULL,
    external_reference VARCHAR(200) NOT NULL DEFAULT '',
    note VARCHAR(500) NOT NULL DEFAULT '',
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_tn_expedition_receipts_public_id (public_id),
    UNIQUE KEY uq_tn_expedition_receipts_trip (trip_id),
    KEY idx_tn_expedition_receipts_status (status),
    CONSTRAINT fk_tn_expedition_receipts_trip FOREIGN KEY (trip_id)
        REFERENCES tn_trips(id) ON DELETE RESTRICT,
    CONSTRAINT fk_tn_expedition_receipts_sent_by FOREIGN KEY (sent_by_account_id)
        REFERENCES tn_operator_accounts(id) ON DELETE SET NULL,
    CONSTRAINT fk_tn_expedition_receipts_returned_by FOREIGN KEY (returned_by_account_id)
        REFERENCES tn_operator_accounts(id) ON DELETE SET NULL,
    CONSTRAINT chk_tn_expedition_receipts_status
        CHECK (status IN ('draft', 'sent', 'returned', 'cancelled'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 6. Связь рейса с приёмкой на площадке: диспетчер выписал, оператор принял.
ALTER TABLE tn_intakes
    ADD COLUMN trip_id BIGINT UNSIGNED NULL AFTER rumex_document_version,
    ADD KEY idx_tn_intakes_trip (trip_id),
    ADD CONSTRAINT fk_tn_intakes_trip FOREIGN KEY (trip_id)
        REFERENCES tn_trips(id) ON DELETE SET NULL;

-- 7. Журнал поступлений денег по рейсу не ведём: стоимость хранится снимком
-- в строке рейса, а справочник цен задаёт значение по умолчанию.
