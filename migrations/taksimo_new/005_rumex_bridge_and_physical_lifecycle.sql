-- Версия 1 серверного контракта РУМЕКС -> новая Таксимо и полный цикл вагона.
-- Данные из старой SQLite-Таксимо сюда не пишутся и не читаются.

ALTER TABLE tn_intakes
    ADD COLUMN rumex_shipment_id BIGINT UNSIGNED NULL AFTER source_reference,
    ADD COLUMN rumex_document_version INT UNSIGNED NULL AFTER rumex_shipment_id,
    ADD COLUMN physical_owner VARCHAR(20) NOT NULL DEFAULT 'taksimo_new' AFTER rumex_document_version,
    ADD COLUMN arrived_at DATETIME(6) NULL AFTER planned_arrival_at,
    ADD COLUMN crane_started_at DATETIME(6) NULL AFTER arrived_at,
    ADD COLUMN crane_ended_at DATETIME(6) NULL AFTER crane_started_at,
    ADD UNIQUE KEY uq_tn_intakes_rumex_document (rumex_shipment_id, rumex_document_version),
    ADD KEY idx_tn_intakes_rumex_shipment (rumex_shipment_id),
    ADD CONSTRAINT chk_tn_intakes_physical_owner
        CHECK (physical_owner IN ('taksimo_new', 'legacy_taksimo'));

DROP TRIGGER IF EXISTS tn_wagons_dispatch_only;
DROP TRIGGER IF EXISTS tn_final_intakes_no_update;

ALTER TABLE tn_wagons
    DROP CHECK chk_tn_wagons_status,
    MODIFY COLUMN status VARCHAR(24) NOT NULL DEFAULT 'forming',
    ADD COLUMN loaded_at DATETIME(6) NULL AFTER created_at,
    ADD COLUMN loaded_by_operator_id BIGINT UNSIGNED NULL AFTER loaded_at,
    ADD COLUMN arrived_kodar_at DATETIME(6) NULL AFTER dispatched_by_operator_id,
    ADD COLUMN arrived_kodar_by_operator_id BIGINT UNSIGNED NULL AFTER arrived_kodar_at,
    ADD COLUMN unloaded_bts_east_at DATETIME(6) NULL AFTER arrived_kodar_by_operator_id,
    ADD COLUMN unloaded_bts_east_by_operator_id BIGINT UNSIGNED NULL AFTER unloaded_bts_east_at;

UPDATE tn_wagons
SET status = CASE status
    WHEN 'loading' THEN 'forming'
    WHEN 'dispatched' THEN 'in_transit'
    ELSE status
END;

ALTER TABLE tn_wagons
    ADD CONSTRAINT chk_tn_wagons_status
        CHECK (status IN ('forming', 'loaded', 'in_transit', 'at_kodar', 'unloaded_bts_east')),
    ADD CONSTRAINT fk_tn_wagons_loaded_by
        FOREIGN KEY (loaded_by_operator_id) REFERENCES tn_operator_accounts(id) ON DELETE RESTRICT,
    ADD CONSTRAINT fk_tn_wagons_arrived_kodar_by
        FOREIGN KEY (arrived_kodar_by_operator_id) REFERENCES tn_operator_accounts(id) ON DELETE RESTRICT,
    ADD CONSTRAINT fk_tn_wagons_unloaded_bts_east_by
        FOREIGN KEY (unloaded_bts_east_by_operator_id) REFERENCES tn_operator_accounts(id) ON DELETE RESTRICT;

CREATE TABLE IF NOT EXISTS tn_integration_outbox_attempts (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    outbox_id BIGINT UNSIGNED NOT NULL,
    success TINYINT(1) NOT NULL,
    result_note VARCHAR(500) NOT NULL DEFAULT '',
    attempted_at DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    KEY idx_tn_integration_outbox_attempts_outbox (outbox_id, attempted_at DESC),
    CONSTRAINT fk_tn_integration_outbox_attempts_outbox
        FOREIGN KEY (outbox_id) REFERENCES tn_integration_outbox(id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS tn_notification_outbox (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    public_id CHAR(36) NOT NULL,
    event_type VARCHAR(80) NOT NULL,
    idempotency_key VARCHAR(200) NOT NULL,
    payload_json JSON NOT NULL,
    created_at DATETIME(6) NOT NULL,
    delivered_at DATETIME(6) NULL,
    receipt_reference VARCHAR(200) NOT NULL DEFAULT '',
    PRIMARY KEY (id),
    UNIQUE KEY uq_tn_notification_outbox_public (public_id),
    UNIQUE KEY uq_tn_notification_outbox_key (idempotency_key),
    KEY idx_tn_notification_outbox_pending (delivered_at, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS tn_notification_attempts (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    notification_id BIGINT UNSIGNED NOT NULL,
    success TINYINT(1) NOT NULL,
    result_note VARCHAR(500) NOT NULL DEFAULT '',
    attempted_at DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    KEY idx_tn_notification_attempts_notification (notification_id, attempted_at DESC),
    CONSTRAINT fk_tn_notification_attempts_notification
        FOREIGN KEY (notification_id) REFERENCES tn_notification_outbox(id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
