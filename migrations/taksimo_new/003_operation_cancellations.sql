-- Неизменяемые отмены подтверждённых загрузок и отправок.
-- Отмена фиксирует юридический факт с причиной и не переписывает исходную операцию.

CREATE TABLE IF NOT EXISTS tn_operation_cancellations (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    public_id CHAR(36) NOT NULL,
    subject_type VARCHAR(40) NOT NULL,
    subject_public_id VARCHAR(80) NOT NULL,
    reason VARCHAR(500) NOT NULL,
    cancelled_by_operator_id BIGINT UNSIGNED NOT NULL,
    cancelled_at DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_tn_operation_cancellations_public (public_id),
    UNIQUE KEY uq_tn_operation_cancellations_subject (subject_type, subject_public_id),
    CONSTRAINT chk_tn_operation_cancellations_subject CHECK (subject_type IN ('intake', 'wagon_load', 'wagon_dispatch')),
    CONSTRAINT fk_tn_operation_cancellations_operator FOREIGN KEY (cancelled_by_operator_id)
        REFERENCES tn_operator_accounts(id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
