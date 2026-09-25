-- Изолированная схема новой PWA Таксимо. Все даты хранятся в UTC.

CREATE TABLE IF NOT EXISTS tn_operator_accounts (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    role_code VARCHAR(20) NOT NULL,
    display_name VARCHAR(120) NOT NULL,
    pin_hash VARCHAR(255) NOT NULL,
    active TINYINT(1) NOT NULL DEFAULT 1,
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_tn_operator_accounts_role (role_code),
    CONSTRAINT chk_tn_operator_accounts_role CHECK (role_code IN ('operator1', 'operator2', 'operator3'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS tn_operator_sessions (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    token_hash CHAR(64) NOT NULL,
    operator_id BIGINT UNSIGNED NOT NULL,
    created_at DATETIME(6) NOT NULL,
    expires_at DATETIME(6) NOT NULL,
    last_seen_at DATETIME(6) NOT NULL,
    revoked_at DATETIME(6) NULL,
    remote_address VARCHAR(128) NOT NULL DEFAULT '',
    user_agent VARCHAR(500) NOT NULL DEFAULT '',
    PRIMARY KEY (id),
    UNIQUE KEY uq_tn_operator_sessions_token (token_hash),
    KEY idx_tn_operator_sessions_active (operator_id, expires_at, revoked_at),
    CONSTRAINT fk_tn_operator_sessions_operator FOREIGN KEY (operator_id) REFERENCES tn_operator_accounts(id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS tn_operator_login_attempts (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    operator_id BIGINT UNSIGNED NULL,
    success TINYINT(1) NOT NULL,
    remote_address VARCHAR(128) NOT NULL DEFAULT '',
    user_agent VARCHAR(500) NOT NULL DEFAULT '',
    attempted_at DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    KEY idx_tn_operator_login_attempts_remote (remote_address, attempted_at),
    CONSTRAINT fk_tn_operator_login_attempts_operator FOREIGN KEY (operator_id) REFERENCES tn_operator_accounts(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS tn_intakes (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    public_id CHAR(36) NOT NULL,
    source_system VARCHAR(40) NOT NULL DEFAULT 'manual',
    source_reference VARCHAR(160) NULL,
    ttn_number VARCHAR(120) NOT NULL DEFAULT '',
    vehicle_plate VARCHAR(40) NOT NULL DEFAULT '',
    driver_name VARCHAR(200) NOT NULL DEFAULT '',
    planned_arrival_at DATETIME(6) NULL,
    expected_blocks_count SMALLINT UNSIGNED NOT NULL,
    status VARCHAR(24) NOT NULL DEFAULT 'draft',
    locked_by_operator_id BIGINT UNSIGNED NULL,
    locked_until DATETIME(6) NULL,
    revision INT UNSIGNED NOT NULL DEFAULT 1,
    created_by_operator_id BIGINT UNSIGNED NULL,
    confirmed_by_operator_id BIGINT UNSIGNED NULL,
    confirmed_at DATETIME(6) NULL,
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_tn_intakes_public (public_id),
    UNIQUE KEY uq_tn_intakes_source (source_system, source_reference),
    KEY idx_tn_intakes_status_created (status, created_at),
    KEY idx_tn_intakes_lock (locked_until),
    CONSTRAINT chk_tn_intakes_count CHECK (expected_blocks_count BETWEEN 1 AND 100),
    CONSTRAINT chk_tn_intakes_status CHECK (status IN ('expected', 'draft', 'in_progress', 'confirmed', 'discrepancy')),
    CONSTRAINT fk_tn_intakes_locked_by FOREIGN KEY (locked_by_operator_id) REFERENCES tn_operator_accounts(id) ON DELETE RESTRICT,
    CONSTRAINT fk_tn_intakes_created_by FOREIGN KEY (created_by_operator_id) REFERENCES tn_operator_accounts(id) ON DELETE SET NULL,
    CONSTRAINT fk_tn_intakes_confirmed_by FOREIGN KEY (confirmed_by_operator_id) REFERENCES tn_operator_accounts(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS tn_intake_lines (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    intake_id BIGINT UNSIGNED NOT NULL,
    sort_order SMALLINT UNSIGNED NOT NULL,
    block_type VARCHAR(30) NOT NULL,
    block_number VARCHAR(80) NOT NULL,
    product_name VARCHAR(200) NOT NULL DEFAULT '',
    weight_kg INT UNSIGNED NULL,
    receipt_state VARCHAR(16) NOT NULL DEFAULT 'received',
    condition_code VARCHAR(16) NOT NULL DEFAULT 'ok',
    discrepancy_note VARCHAR(500) NOT NULL DEFAULT '',
    yard_x TINYINT UNSIGNED NULL,
    yard_y TINYINT UNSIGNED NULL,
    confirmed_at DATETIME(6) NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_tn_intake_lines_block (intake_id, block_type, block_number),
    KEY idx_tn_intake_lines_intake (intake_id, sort_order),
    CONSTRAINT chk_tn_intake_lines_state CHECK (receipt_state IN ('received', 'missing', 'damaged')),
    CONSTRAINT chk_tn_intake_lines_condition CHECK (condition_code IN ('ok', 'damage')),
    CONSTRAINT chk_tn_intake_lines_x CHECK (yard_x IS NULL OR yard_x BETWEEN 1 AND 13),
    CONSTRAINT chk_tn_intake_lines_y CHECK (yard_y IS NULL OR yard_y BETWEEN 1 AND 25),
    CONSTRAINT fk_tn_intake_lines_intake FOREIGN KEY (intake_id) REFERENCES tn_intakes(id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS tn_expected_intake_lines (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    intake_id BIGINT UNSIGNED NOT NULL,
    sort_order SMALLINT UNSIGNED NOT NULL,
    block_type VARCHAR(30) NOT NULL,
    block_number VARCHAR(80) NOT NULL,
    product_name VARCHAR(200) NOT NULL DEFAULT '',
    weight_kg INT UNSIGNED NULL,
    created_at DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_tn_expected_intake_lines_block (intake_id, block_type, block_number),
    KEY idx_tn_expected_intake_lines_intake (intake_id, sort_order),
    CONSTRAINT fk_tn_expected_intake_lines_intake FOREIGN KEY (intake_id) REFERENCES tn_intakes(id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS tn_blocks (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    block_type VARCHAR(30) NOT NULL,
    block_number VARCHAR(80) NOT NULL,
    product_name VARCHAR(200) NOT NULL DEFAULT '',
    weight_kg INT UNSIGNED NULL,
    condition_code VARCHAR(16) NOT NULL DEFAULT 'ok',
    current_location_kind VARCHAR(16) NOT NULL,
    yard_x TINYINT UNSIGNED NULL,
    yard_y TINYINT UNSIGNED NULL,
    yard_slot TINYINT UNSIGNED NULL,
    wagon_number VARCHAR(40) NULL,
    received_intake_id BIGINT UNSIGNED NOT NULL,
    received_line_id BIGINT UNSIGNED NOT NULL,
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_tn_blocks_identity (block_type, block_number),
    UNIQUE KEY uq_tn_blocks_yard_slot (current_location_kind, yard_x, yard_y, yard_slot),
    KEY idx_tn_blocks_location (current_location_kind, yard_x, yard_y),
    KEY idx_tn_blocks_wagon (wagon_number),
    CONSTRAINT chk_tn_blocks_location CHECK (current_location_kind IN ('yard', 'wagon')),
    CONSTRAINT chk_tn_blocks_yard_slot CHECK (yard_slot IS NULL OR yard_slot BETWEEN 1 AND 4),
    CONSTRAINT fk_tn_blocks_intake FOREIGN KEY (received_intake_id) REFERENCES tn_intakes(id) ON DELETE RESTRICT,
    CONSTRAINT fk_tn_blocks_line FOREIGN KEY (received_line_id) REFERENCES tn_intake_lines(id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS tn_wagons (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    wagon_number VARCHAR(40) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'loading',
    created_at DATETIME(6) NOT NULL,
    dispatched_at DATETIME(6) NULL,
    dispatched_by_operator_id BIGINT UNSIGNED NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_tn_wagons_number (wagon_number),
    CONSTRAINT chk_tn_wagons_status CHECK (status IN ('loading', 'dispatched')),
    CONSTRAINT fk_tn_wagons_dispatched_by FOREIGN KEY (dispatched_by_operator_id) REFERENCES tn_operator_accounts(id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS tn_wagon_loads (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    wagon_id BIGINT UNSIGNED NOT NULL,
    block_id BIGINT UNSIGNED NOT NULL,
    loaded_by_operator_id BIGINT UNSIGNED NOT NULL,
    loaded_at DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_tn_wagon_loads_block (block_id),
    KEY idx_tn_wagon_loads_wagon (wagon_id, loaded_at),
    CONSTRAINT fk_tn_wagon_loads_wagon FOREIGN KEY (wagon_id) REFERENCES tn_wagons(id) ON DELETE RESTRICT,
    CONSTRAINT fk_tn_wagon_loads_block FOREIGN KEY (block_id) REFERENCES tn_blocks(id) ON DELETE RESTRICT,
    CONSTRAINT fk_tn_wagon_loads_operator FOREIGN KEY (loaded_by_operator_id) REFERENCES tn_operator_accounts(id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS tn_intake_cancellations (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    intake_id BIGINT UNSIGNED NOT NULL,
    reason VARCHAR(500) NOT NULL,
    cancelled_by_operator_id BIGINT UNSIGNED NOT NULL,
    cancelled_at DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_tn_intake_cancellations_intake (intake_id),
    CONSTRAINT fk_tn_intake_cancellations_intake FOREIGN KEY (intake_id) REFERENCES tn_intakes(id) ON DELETE RESTRICT,
    CONSTRAINT fk_tn_intake_cancellations_operator FOREIGN KEY (cancelled_by_operator_id) REFERENCES tn_operator_accounts(id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS tn_intake_corrections (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    public_id CHAR(36) NOT NULL,
    intake_id BIGINT UNSIGNED NOT NULL,
    reason VARCHAR(500) NOT NULL,
    correction_json JSON NOT NULL,
    created_by_operator_id BIGINT UNSIGNED NOT NULL,
    created_at DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_tn_intake_corrections_public (public_id),
    KEY idx_tn_intake_corrections_intake (intake_id, created_at),
    CONSTRAINT fk_tn_intake_corrections_intake FOREIGN KEY (intake_id) REFERENCES tn_intakes(id) ON DELETE RESTRICT,
    CONSTRAINT fk_tn_intake_corrections_operator FOREIGN KEY (created_by_operator_id) REFERENCES tn_operator_accounts(id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS tn_operation_corrections (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    public_id CHAR(36) NOT NULL,
    subject_type VARCHAR(40) NOT NULL,
    subject_public_id CHAR(36) NOT NULL,
    reason VARCHAR(500) NOT NULL,
    correction_json JSON NOT NULL,
    created_by_operator_id BIGINT UNSIGNED NOT NULL,
    created_at DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_tn_operation_corrections_public (public_id),
    KEY idx_tn_operation_corrections_subject (subject_type, subject_public_id, created_at),
    CONSTRAINT chk_tn_operation_corrections_subject CHECK (subject_type IN ('intake', 'wagon_load', 'wagon_dispatch')),
    CONSTRAINT fk_tn_operation_corrections_operator FOREIGN KEY (created_by_operator_id) REFERENCES tn_operator_accounts(id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS tn_attachments (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    public_id CHAR(36) NOT NULL,
    intake_id BIGINT UNSIGNED NULL,
    wagon_id BIGINT UNSIGNED NULL,
    attachment_kind VARCHAR(20) NOT NULL,
    original_object_key VARCHAR(500) NOT NULL,
    preview_object_key VARCHAR(500) NULL,
    original_sha256 CHAR(64) NOT NULL,
    original_size_bytes BIGINT UNSIGNED NOT NULL,
    content_type VARCHAR(120) NOT NULL,
    created_by_operator_id BIGINT UNSIGNED NOT NULL,
    created_at DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_tn_attachments_public (public_id),
    CONSTRAINT chk_tn_attachments_subject CHECK ((intake_id IS NOT NULL) <> (wagon_id IS NOT NULL)),
    CONSTRAINT chk_tn_attachments_kind CHECK (attachment_kind IN ('ttn', 'cargo', 'damage')),
    CONSTRAINT fk_tn_attachments_intake FOREIGN KEY (intake_id) REFERENCES tn_intakes(id) ON DELETE RESTRICT,
    CONSTRAINT fk_tn_attachments_wagon FOREIGN KEY (wagon_id) REFERENCES tn_wagons(id) ON DELETE RESTRICT,
    CONSTRAINT fk_tn_attachments_operator FOREIGN KEY (created_by_operator_id) REFERENCES tn_operator_accounts(id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS tn_events (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    public_id CHAR(36) NOT NULL,
    event_type VARCHAR(80) NOT NULL,
    subject_type VARCHAR(40) NOT NULL,
    subject_public_id CHAR(36) NULL,
    actor_kind VARCHAR(30) NOT NULL,
    actor_id VARCHAR(120) NOT NULL DEFAULT '',
    actor_name VARCHAR(120) NOT NULL DEFAULT '',
    payload_json JSON NOT NULL,
    occurred_at DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_tn_events_public (public_id),
    KEY idx_tn_events_subject (subject_type, subject_public_id, occurred_at),
    KEY idx_tn_events_occurred (occurred_at, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS tn_integration_inbox (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    idempotency_key VARCHAR(160) NOT NULL,
    source_system VARCHAR(40) NOT NULL,
    received_at DATETIME(6) NOT NULL,
    payload_json JSON NOT NULL,
    intake_id BIGINT UNSIGNED NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_tn_integration_inbox_key (source_system, idempotency_key),
    CONSTRAINT fk_tn_integration_inbox_intake FOREIGN KEY (intake_id) REFERENCES tn_intakes(id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS tn_integration_outbox (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    public_id CHAR(36) NOT NULL,
    event_type VARCHAR(80) NOT NULL,
    idempotency_key VARCHAR(200) NOT NULL,
    payload_json JSON NOT NULL,
    created_at DATETIME(6) NOT NULL,
    delivered_at DATETIME(6) NULL,
    receipt_reference VARCHAR(200) NOT NULL DEFAULT '',
    PRIMARY KEY (id),
    UNIQUE KEY uq_tn_integration_outbox_public (public_id),
    UNIQUE KEY uq_tn_integration_outbox_key (idempotency_key),
    KEY idx_tn_integration_outbox_pending (delivered_at, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
