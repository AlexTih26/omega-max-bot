-- 015: Очередь машин и раздельные состояния рейса.
--
-- Зависимости: миграции 001--014, включая tn_roles и tn_role_permissions из 014.
-- Миграция source-only и forward-only: приложение не применяет её автоматически.
-- Целевая СУБД: MySQL 8, InnoDB, utf8mb4. Запуск разрешён только технической
-- учётной записью через штатный runner после подтверждения состояния зависимостей.
--
-- Legacy tn_trips.status и его CHECK намеренно не меняются. Исторические строки
-- не переписываются: document_status остаётся NULL, пока не появится утверждённый
-- writer нового контура.

-- 1. Аддитивный документный автомат без неявного отображения legacy-статусов.
ALTER TABLE tn_trips
    ADD COLUMN document_status VARCHAR(24) NULL AFTER status,
    ADD CONSTRAINT chk_tn_trips_document_status
        CHECK (
            document_status IS NULL
            OR document_status IN (
                'draft',
                'ttn_issued',
                'ttn_printed',
                'accountant_review',
                'er_sent',
                'approved',
                'documents_printed',
                'handed_to_driver',
                'cancelled'
            )
        );

-- 2. Одна строка хранит один цикл очереди и не удаляется после терминального
-- состояния. active_vehicle_id выражает единственность только активного цикла.
CREATE TABLE tn_vehicle_queue_entries (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    public_id CHAR(36) NOT NULL,
    vehicle_id BIGINT UNSIGNED NOT NULL,
    state VARCHAR(24) NOT NULL,
    assigned_trip_id BIGINT UNSIGNED NULL,
    intake_id BIGINT UNSIGNED NULL,
    enqueued_at DATETIME(6) NOT NULL,
    assigned_at DATETIME(6) NULL,
    departed_at DATETIME(6) NULL,
    arrived_taksimo_at DATETIME(6) NULL,
    received_at DATETIME(6) NULL,
    returned_at DATETIME(6) NULL,
    cancelled_at DATETIME(6) NULL,
    created_by_account_id BIGINT UNSIGNED NOT NULL,
    transition_by_account_id BIGINT UNSIGNED NULL,
    cancelled_by_account_id BIGINT UNSIGNED NULL,
    cancellation_reason VARCHAR(500) NOT NULL DEFAULT '',
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    active_vehicle_id BIGINT UNSIGNED GENERATED ALWAYS AS (
        CASE
            WHEN state IN ('queued', 'assigned_to_trip', 'departed', 'arrived_taksimo', 'received')
                THEN vehicle_id
            ELSE NULL
        END
    ) STORED,
    PRIMARY KEY (id),
    UNIQUE KEY uq_tn_vehicle_queue_entries_public_id (public_id),
    UNIQUE KEY uq_tn_vehicle_queue_entries_active_vehicle (active_vehicle_id),
    KEY idx_tn_vehicle_queue_entries_vehicle_created (vehicle_id, created_at),
    KEY idx_tn_vehicle_queue_entries_assigned_trip (assigned_trip_id),
    KEY idx_tn_vehicle_queue_entries_intake (intake_id),
    KEY idx_tn_vehicle_queue_entries_state_enqueued (state, enqueued_at),
    CONSTRAINT chk_tn_vehicle_queue_entries_state
        CHECK (state IN (
            'queued',
            'assigned_to_trip',
            'departed',
            'arrived_taksimo',
            'received',
            'returned_to_queue',
            'cancelled'
        )),
    CONSTRAINT chk_tn_vehicle_queue_entries_cancel_reason
        CHECK (state <> 'cancelled' OR cancellation_reason <> ''),
    CONSTRAINT fk_tn_vehicle_queue_entries_vehicle
        FOREIGN KEY (vehicle_id) REFERENCES tn_vehicles(id) ON DELETE RESTRICT,
    CONSTRAINT fk_tn_vehicle_queue_entries_assigned_trip
        FOREIGN KEY (assigned_trip_id) REFERENCES tn_trips(id) ON DELETE SET NULL,
    CONSTRAINT fk_tn_vehicle_queue_entries_intake
        FOREIGN KEY (intake_id) REFERENCES tn_intakes(id) ON DELETE SET NULL,
    CONSTRAINT fk_tn_vehicle_queue_entries_created_by
        FOREIGN KEY (created_by_account_id) REFERENCES tn_operator_accounts(id) ON DELETE RESTRICT,
    CONSTRAINT fk_tn_vehicle_queue_entries_transition_by
        FOREIGN KEY (transition_by_account_id) REFERENCES tn_operator_accounts(id) ON DELETE RESTRICT,
    CONSTRAINT fk_tn_vehicle_queue_entries_cancelled_by
        FOREIGN KEY (cancelled_by_account_id) REFERENCES tn_operator_accounts(id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 3. Начальное создание и каждый переход записываются отдельной строкой.
-- Межстрочный граф переходов и порядок временных меток проверяет будущая команда;
-- триггеры append-only для этой таблицы намеренно отложены до 016.
CREATE TABLE tn_vehicle_queue_transitions (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    queue_entry_id BIGINT UNSIGNED NOT NULL,
    from_state VARCHAR(24) NULL,
    to_state VARCHAR(24) NOT NULL,
    actor_account_id BIGINT UNSIGNED NOT NULL,
    reason VARCHAR(500) NOT NULL DEFAULT '',
    occurred_at DATETIME(6) NOT NULL,
    details_json JSON NULL,
    PRIMARY KEY (id),
    KEY idx_tn_vehicle_queue_transitions_entry_occurred (queue_entry_id, occurred_at),
    KEY idx_tn_vehicle_queue_transitions_to_state_occurred (to_state, occurred_at),
    KEY idx_tn_vehicle_queue_transitions_actor_occurred (actor_account_id, occurred_at),
    CONSTRAINT chk_tn_vehicle_queue_transitions_from_state
        CHECK (from_state IS NULL OR from_state IN (
            'queued',
            'assigned_to_trip',
            'departed',
            'arrived_taksimo',
            'received',
            'returned_to_queue',
            'cancelled'
        )),
    CONSTRAINT chk_tn_vehicle_queue_transitions_to_state
        CHECK (to_state IN (
            'queued',
            'assigned_to_trip',
            'departed',
            'arrived_taksimo',
            'received',
            'returned_to_queue',
            'cancelled'
        )),
    CONSTRAINT fk_tn_vehicle_queue_transitions_entry
        FOREIGN KEY (queue_entry_id) REFERENCES tn_vehicle_queue_entries(id) ON DELETE RESTRICT,
    CONSTRAINT fk_tn_vehicle_queue_transitions_actor
        FOREIGN KEY (actor_account_id) REFERENCES tn_operator_accounts(id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 4. Только утверждённые capability очереди. Они не подключают runtime-проверки.
INSERT INTO tn_role_permissions (
    role_id,
    capability,
    can_view,
    can_edit,
    can_print,
    created_at,
    updated_at
)
SELECT role.id, 'queue.entry.create', 0, 1, 0, UTC_TIMESTAMP(6), UTC_TIMESTAMP(6)
FROM tn_roles AS role
WHERE role.code IN ('dispatcher');

INSERT INTO tn_role_permissions (
    role_id,
    capability,
    can_view,
    can_edit,
    can_print,
    created_at,
    updated_at
)
SELECT role.id, 'queue.entry.view', 1, 0, 0, UTC_TIMESTAMP(6), UTC_TIMESTAMP(6)
FROM tn_roles AS role
WHERE role.code IN ('admin', 'dispatcher');

INSERT INTO tn_role_permissions (
    role_id,
    capability,
    can_view,
    can_edit,
    can_print,
    created_at,
    updated_at
)
SELECT role.id, 'queue.entry.cancel', 0, 1, 0, UTC_TIMESTAMP(6), UTC_TIMESTAMP(6)
FROM tn_roles AS role
WHERE role.code IN ('admin');

INSERT INTO tn_role_permissions (
    role_id,
    capability,
    can_view,
    can_edit,
    can_print,
    created_at,
    updated_at
)
SELECT role.id, 'queue.transition', 0, 1, 0, UTC_TIMESTAMP(6), UTC_TIMESTAMP(6)
FROM tn_roles AS role
WHERE role.code IN ('dispatcher', 'operator');

INSERT INTO tn_role_permissions (
    role_id,
    capability,
    can_view,
    can_edit,
    can_print,
    created_at,
    updated_at
)
SELECT role.id, 'queue.history.view', 1, 0, 0, UTC_TIMESTAMP(6), UTC_TIMESTAMP(6)
FROM tn_roles AS role
WHERE role.code IN ('admin', 'dispatcher');
