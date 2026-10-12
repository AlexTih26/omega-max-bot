-- 009: Тупики вагонов новой площадки и одноразовый архив старой Таксимо.
-- Старая SQLite-база никогда не изменяется: JSON-снимок передаёт в MySQL
-- только приложение после даты перехода и только через read-only соединение.
--
-- Тупики наследуют старую площадку (ГРУЗОВОЙ, ТУРАН) и дополняются третьим.
-- Коды совпадают с tn_dead_ends из 010: gruzovoy, turan, third.

CREATE TABLE IF NOT EXISTS tn_wagon_yard_slots (
    dead_end_code VARCHAR(40) NOT NULL,
    slot_index TINYINT UNSIGNED NOT NULL,
    PRIMARY KEY (dead_end_code, slot_index),
    CONSTRAINT chk_tn_wagon_yard_slots_code
        CHECK (dead_end_code IN ('gruzovoy', 'turan', 'third')),
    CONSTRAINT chk_tn_wagon_yard_slots_index CHECK (slot_index BETWEEN 1 AND 10)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

INSERT IGNORE INTO tn_wagon_yard_slots (dead_end_code, slot_index) VALUES
    ('gruzovoy', 1), ('gruzovoy', 2), ('gruzovoy', 3), ('gruzovoy', 4), ('gruzovoy', 5),
    ('gruzovoy', 6), ('gruzovoy', 7), ('gruzovoy', 8), ('gruzovoy', 9), ('gruzovoy', 10),
    ('turan', 1), ('turan', 2), ('turan', 3), ('turan', 4), ('turan', 5),
    ('turan', 6), ('turan', 7), ('turan', 8), ('turan', 9), ('turan', 10),
    ('third', 1), ('third', 2), ('third', 3), ('third', 4), ('third', 5),
    ('third', 6), ('third', 7), ('third', 8), ('third', 9), ('third', 10);

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
