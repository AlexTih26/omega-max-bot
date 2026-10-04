-- 008: Документы (копии ТТН), прикладываемые оператору новой Таксимо.
-- РУМЕКС после присвоения номера ТТН передаёт через служебный мост готовые
-- xlsx-копии; здесь они хранятся как неизменяемые вложения приёмки.

CREATE TABLE IF NOT EXISTS tn_intake_documents (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    intake_id BIGINT UNSIGNED NOT NULL,
    document_kind VARCHAR(16) NOT NULL CHECK (document_kind IN ('TN', 'ER')),
    copy_number TINYINT UNSIGNED NOT NULL,
    filename VARCHAR(255) NOT NULL,
    content_type VARCHAR(128) NOT NULL,
    content LONGBLOB NOT NULL,
    idempotency_key VARCHAR(160) NOT NULL,
    created_at DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_tn_intake_document_copy (intake_id, document_kind, copy_number),
    UNIQUE KEY uq_tn_intake_document_idem (idempotency_key),
    KEY idx_tn_intake_documents_intake (intake_id),
    CONSTRAINT fk_tn_intake_documents_intake FOREIGN KEY (intake_id)
        REFERENCES tn_intakes (id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
