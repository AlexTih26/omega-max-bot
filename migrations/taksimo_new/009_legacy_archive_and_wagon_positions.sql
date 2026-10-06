-- 009: Независимые позиции вагонов и одноразовый архив старой Таксимо.
-- Старая SQLite-база никогда не изменяется: JSON-снимок передаёт в MySQL
-- только приложение после даты перехода и только через read-only соединение.

CREATE TABLE IF NOT EXISTS tn_wagon_yard_slots (
    dead_end_code VARCHAR(40) NOT NULL,
    slot_index TINYINT UNSIGNED NOT NULL,
    PRIMARY KEY (dead_end_code, slot_index),
    CONSTRAINT chk_tn_wagon_yard_slots_code
        CHECK (dead_end_code IN ('gruzovoy_1', 'gruzovoy_2', 'gruzovoy_3')),
    CONSTRAINT chk_tn_wagon_yard_slots_index CHECK (slot_index BETWEEN 1 AND 10)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

INSERT IGNORE INTO tn_wagon_yard_slots (dead_end_code, slot_index) VALUES
    ('gruzovoy_1', 1), ('gruzovoy_1', 2), ('gruzovoy_1', 3), ('gruzovoy_1', 4), ('gruzovoy_1', 5),
    ('gruzovoy_1', 6), ('gruzovoy_1', 7), ('gruzovoy_1', 8), ('gruzovoy_1', 9), ('gruzovoy_1', 10),
    ('gruzovoy_2', 1), ('gruzovoy_2', 2), ('gruzovoy_2', 3), ('gruzovoy_2', 4), ('gruzovoy_2', 5),
    ('gruzovoy_2', 6), ('gruzovoy_2', 7), ('gruzovoy_2', 8), ('gruzovoy_2', 9), ('gruzovoy_2', 10),
    ('gruzovoy_3', 1), ('gruzovoy_3', 2), ('gruzovoy_3', 3), ('gruzovoy_3', 4), ('gruzovoy_3', 5),
    ('gruzovoy_3', 6), ('gruzovoy_3', 7), ('gruzovoy_3', 8), ('gruzovoy_3', 9), ('gruzovoy_3', 10);

CREATE TABLE IF NOT EXISTS tn_wagon_position_history (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    wagon_id BIGINT UNSIGNED NOT NULL,
    dead_end_code VARCHAR(40) NOT NULL,
    slot_index TINYINT UNSIGNED NOT NULL,
    assigned_by_operator_id BIGINT UNSIGNED NOT NULL,
    assigned_at DATETIME(6) NOT NULL,
    released_by_operator_id BIGINT UNSIGNED NULL,
    released_at DATETIME(6) NULL,
    active_marker TINYINT UNSIGNED NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_tn_wagon_position_active_slot (dead_end_code, slot_index, active_marker),
    UNIQUE KEY uq_tn_wagon_position_active_wagon (wagon_id, active_marker),
    KEY idx_tn_wagon_position_history_wagon (wagon_id, assigned_at),
    CONSTRAINT chk_tn_wagon_position_active
        CHECK ((active_marker = 1 AND released_at IS NULL AND released_by_operator_id IS NULL)
            OR (active_marker IS NULL AND released_at IS NOT NULL AND released_by_operator_id IS NOT NULL)),
    CONSTRAINT fk_tn_wagon_position_wagon FOREIGN KEY (wagon_id)
        REFERENCES tn_wagons(id) ON DELETE RESTRICT,
    CONSTRAINT fk_tn_wagon_position_slot FOREIGN KEY (dead_end_code, slot_index)
        REFERENCES tn_wagon_yard_slots(dead_end_code, slot_index) ON DELETE RESTRICT,
    CONSTRAINT fk_tn_wagon_position_assigned_by FOREIGN KEY (assigned_by_operator_id)
        REFERENCES tn_operator_accounts(id) ON DELETE RESTRICT,
    CONSTRAINT fk_tn_wagon_position_released_by FOREIGN KEY (released_by_operator_id)
        REFERENCES tn_operator_accounts(id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS tn_legacy_taksimo_snapshots (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    archive_key VARCHAR(40) NOT NULL,
    payload_json JSON NOT NULL,
    cutover_at DATETIME(6) NOT NULL,
    captured_at DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_tn_legacy_taksimo_snapshots_key (archive_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
