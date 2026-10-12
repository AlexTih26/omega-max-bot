-- 013: Сквозная история блока и машины.
-- Этап 6 единой модели: у блока, машины и вагона есть история.
--
-- Миграция не применяется автоматически: запуск только технической учётной
-- записью через scripts/migrate-taksimo-new-mysql.sh.

-- 1. Паспорт блока: буква плюс номер уникальны, id живёт с рождения.
-- Блок может быть заведён диспетчером в рейсе ещё до приёмки на площадке,
-- поэтому паспорт не зависит от приёмки.
CREATE TABLE IF NOT EXISTS tn_block_registry (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    block_key VARCHAR(80) NOT NULL,
    block_letter CHAR(1) NOT NULL,
    block_number VARCHAR(40) NOT NULL,
    suffix_code VARCHAR(8) NOT NULL DEFAULT '',
    weight_kg INT UNSIGNED NULL,
    price_rub DECIMAL(12, 2) NOT NULL DEFAULT 0.00,
    state VARCHAR(24) NOT NULL DEFAULT 'declared',
    first_trip_id BIGINT UNSIGNED NULL,
    first_account_id BIGINT UNSIGNED NULL,
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_tn_block_registry_key (block_key),
    KEY idx_tn_block_registry_letter_number (block_letter, block_number),
    KEY idx_tn_block_registry_state (state),
    CONSTRAINT fk_tn_block_registry_trip FOREIGN KEY (first_trip_id)
        REFERENCES tn_trips(id) ON DELETE SET NULL,
    CONSTRAINT fk_tn_block_registry_account FOREIGN KEY (first_account_id)
        REFERENCES tn_operator_accounts(id) ON DELETE SET NULL,
    CONSTRAINT chk_tn_block_registry_letter CHECK (block_letter IN ('A', 'B', 'C', 'D', 'E', 'F', 'K')),
    CONSTRAINT chk_tn_block_registry_state
        CHECK (state IN ('declared', 'in_transit', 'at_yard', 'in_wagon', 'in_kodar', 'unloaded_bts_east', 'returned')),
    CONSTRAINT chk_tn_block_registry_weight CHECK (weight_kg IS NULL OR weight_kg > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 2. История перемещений блока: каждое событие отдельной строкой.
-- Подтверждённая история не переписывается: исправление добавляет событие.
CREATE TABLE IF NOT EXISTS tn_block_history (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    block_id BIGINT UNSIGNED NOT NULL,
    event_type VARCHAR(40) NOT NULL,
    location_kind VARCHAR(24) NOT NULL DEFAULT '',
    trip_id BIGINT UNSIGNED NULL,
    wagon_number VARCHAR(40) NOT NULL DEFAULT '',
    yard_x TINYINT UNSIGNED NULL,
    yard_y TINYINT UNSIGNED NULL,
    details_json JSON NULL,
    actor_kind VARCHAR(30) NOT NULL DEFAULT '',
    actor_id VARCHAR(120) NOT NULL DEFAULT '',
    actor_name VARCHAR(120) NOT NULL DEFAULT '',
    occurred_at DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    KEY idx_tn_block_history_block (block_id, occurred_at),
    KEY idx_tn_block_history_type (event_type, occurred_at),
    KEY idx_tn_block_history_wagon (wagon_number, occurred_at),
    CONSTRAINT fk_tn_block_history_block FOREIGN KEY (block_id)
        REFERENCES tn_block_registry(id) ON DELETE RESTRICT,
    CONSTRAINT fk_tn_block_history_trip FOREIGN KEY (trip_id)
        REFERENCES tn_trips(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 3. Связь строки рейса с паспортом блока.
ALTER TABLE tn_trip_blocks
    ADD COLUMN registry_block_id BIGINT UNSIGNED NULL AFTER block_id,
    ADD KEY idx_tn_trip_blocks_registry (registry_block_id),
    ADD CONSTRAINT fk_tn_trip_blocks_registry FOREIGN KEY (registry_block_id)
        REFERENCES tn_block_registry(id) ON DELETE SET NULL;

-- 4. История машины читается из рейсов, поэтому нужен индекс по машине
-- вместе с датой: запрос «сколько раз сходила» идёт по журналу рейсов.
ALTER TABLE tn_trips
    ADD KEY idx_tn_trips_vehicle_date (vehicle_id, trip_date),
    ADD KEY idx_tn_trips_plate_date (vehicle_plate, trip_date);

-- 5. Приёмка связывает блоки площадки с паспортом: поиск по истории
-- начинается с буквы и номера, а не с внутреннего id приёмки.
ALTER TABLE tn_blocks
    ADD COLUMN registry_block_id BIGINT UNSIGNED NULL AFTER block_number,
    ADD KEY idx_tn_blocks_registry (registry_block_id),
    ADD CONSTRAINT fk_tn_blocks_registry FOREIGN KEY (registry_block_id)
        REFERENCES tn_block_registry(id) ON DELETE SET NULL;
