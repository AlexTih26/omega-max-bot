-- Защита неизменяемых полей нового контракта и полного физического цикла.

DELIMITER //

CREATE TRIGGER tn_intakes_physical_facts_only BEFORE UPDATE ON tn_intakes
FOR EACH ROW BEGIN
    IF OLD.source_system = 'rumex' AND NOT (
        NEW.id <=> OLD.id
        AND NEW.public_id <=> OLD.public_id
        AND NEW.source_system <=> OLD.source_system
        AND NEW.source_reference <=> OLD.source_reference
        AND NEW.rumex_shipment_id <=> OLD.rumex_shipment_id
        AND NEW.rumex_document_version <=> OLD.rumex_document_version
        AND NEW.physical_owner <=> OLD.physical_owner
        AND NEW.ttn_number <=> OLD.ttn_number
        AND NEW.vehicle_plate <=> OLD.vehicle_plate
        AND NEW.driver_name <=> OLD.driver_name
        AND NEW.planned_arrival_at <=> OLD.planned_arrival_at
        AND (
            NEW.arrived_at <=> OLD.arrived_at
            OR (OLD.arrived_at IS NULL AND NEW.arrived_at IS NOT NULL)
        )
        AND (
            NEW.crane_started_at <=> OLD.crane_started_at
            OR (OLD.crane_started_at IS NULL AND NEW.crane_started_at IS NOT NULL)
        )
        AND (
            NEW.crane_ended_at <=> OLD.crane_ended_at
            OR (OLD.crane_ended_at IS NULL AND NEW.crane_ended_at IS NOT NULL)
        )
        AND NEW.expected_blocks_count <=> OLD.expected_blocks_count
        AND NEW.revision <=> OLD.revision
        AND NEW.created_by_operator_id <=> OLD.created_by_operator_id
        AND NEW.created_at <=> OLD.created_at
    ) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Снимок РУМЕКС в приёмке нельзя изменять';
    END IF;
END//

CREATE TRIGGER tn_final_intakes_document_fields_only BEFORE UPDATE ON tn_intakes
FOR EACH ROW BEGIN
    IF OLD.status IN ('confirmed', 'discrepancy') AND NOT (
        NEW.id <=> OLD.id
        AND NEW.public_id <=> OLD.public_id
        AND NEW.source_system <=> OLD.source_system
        AND NEW.source_reference <=> OLD.source_reference
        AND NEW.rumex_shipment_id <=> OLD.rumex_shipment_id
        AND NEW.rumex_document_version <=> OLD.rumex_document_version
        AND NEW.physical_owner <=> OLD.physical_owner
        AND NEW.ttn_number <=> OLD.ttn_number
        AND NEW.vehicle_plate <=> OLD.vehicle_plate
        AND NEW.driver_name <=> OLD.driver_name
        AND NEW.planned_arrival_at <=> OLD.planned_arrival_at
        AND NEW.arrived_at <=> OLD.arrived_at
        AND NEW.crane_started_at <=> OLD.crane_started_at
        AND NEW.crane_ended_at <=> OLD.crane_ended_at
        AND NEW.expected_blocks_count <=> OLD.expected_blocks_count
        AND NEW.status <=> OLD.status
        AND NEW.locked_by_operator_id <=> OLD.locked_by_operator_id
        AND NEW.locked_until <=> OLD.locked_until
        AND NEW.revision <=> OLD.revision
        AND NEW.created_by_operator_id <=> OLD.created_by_operator_id
        AND NEW.confirmed_by_operator_id <=> OLD.confirmed_by_operator_id
        AND NEW.confirmed_at <=> OLD.confirmed_at
        AND NEW.created_at <=> OLD.created_at
        AND NEW.updated_at <=> OLD.updated_at
    ) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Подтверждённую приёмку нельзя изменять';
    END IF;
END//

CREATE TRIGGER tn_wagons_full_lifecycle_only BEFORE UPDATE ON tn_wagons
FOR EACH ROW BEGIN
    IF NOT (
        NEW.id <=> OLD.id
        AND NEW.wagon_number <=> OLD.wagon_number
        AND NEW.created_at <=> OLD.created_at
        AND (
            (
                OLD.status = 'forming'
                AND NEW.status = 'loaded'
                AND OLD.loaded_at IS NULL
                AND NEW.loaded_at IS NOT NULL
                AND OLD.loaded_by_operator_id IS NULL
                AND NEW.loaded_by_operator_id IS NOT NULL
                AND NEW.dispatched_at IS NULL
                AND NEW.arrived_kodar_at IS NULL
                AND NEW.unloaded_bts_east_at IS NULL
            ) OR (
                OLD.status = 'loaded'
                AND NEW.status = 'in_transit'
                AND NEW.loaded_at <=> OLD.loaded_at
                AND NEW.loaded_by_operator_id <=> OLD.loaded_by_operator_id
                AND OLD.dispatched_at IS NULL
                AND NEW.dispatched_at IS NOT NULL
                AND OLD.dispatched_by_operator_id IS NULL
                AND NEW.dispatched_by_operator_id IS NOT NULL
                AND NEW.arrived_kodar_at IS NULL
                AND NEW.arrived_kodar_by_operator_id IS NULL
                AND NEW.unloaded_bts_east_at IS NULL
                AND NEW.unloaded_bts_east_by_operator_id IS NULL
            ) OR (
                OLD.status = 'in_transit'
                AND NEW.status = 'at_kodar'
                AND NEW.loaded_at <=> OLD.loaded_at
                AND NEW.loaded_by_operator_id <=> OLD.loaded_by_operator_id
                AND NEW.dispatched_at <=> OLD.dispatched_at
                AND NEW.dispatched_by_operator_id <=> OLD.dispatched_by_operator_id
                AND OLD.arrived_kodar_at IS NULL
                AND NEW.arrived_kodar_at IS NOT NULL
                AND OLD.arrived_kodar_by_operator_id IS NULL
                AND NEW.arrived_kodar_by_operator_id IS NOT NULL
                AND NEW.unloaded_bts_east_at IS NULL
                AND NEW.unloaded_bts_east_by_operator_id IS NULL
            ) OR (
                OLD.status = 'at_kodar'
                AND NEW.status = 'unloaded_bts_east'
                AND NEW.loaded_at <=> OLD.loaded_at
                AND NEW.loaded_by_operator_id <=> OLD.loaded_by_operator_id
                AND NEW.dispatched_at <=> OLD.dispatched_at
                AND NEW.dispatched_by_operator_id <=> OLD.dispatched_by_operator_id
                AND NEW.arrived_kodar_at <=> OLD.arrived_kodar_at
                AND NEW.arrived_kodar_by_operator_id <=> OLD.arrived_kodar_by_operator_id
                AND OLD.unloaded_bts_east_at IS NULL
                AND NEW.unloaded_bts_east_at IS NOT NULL
                AND OLD.unloaded_bts_east_by_operator_id IS NULL
                AND NEW.unloaded_bts_east_by_operator_id IS NOT NULL
            )
        )
    ) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Недопустимый переход статуса вагона';
    END IF;
END//

CREATE TRIGGER tn_notification_outbox_ack_only BEFORE UPDATE ON tn_notification_outbox
FOR EACH ROW BEGIN
    IF NOT (
        NEW.id <=> OLD.id
        AND NEW.public_id <=> OLD.public_id
        AND NEW.event_type <=> OLD.event_type
        AND NEW.idempotency_key <=> OLD.idempotency_key
        AND NEW.payload_json <=> OLD.payload_json
        AND NEW.created_at <=> OLD.created_at
        AND OLD.delivered_at IS NULL
        AND NEW.delivered_at IS NOT NULL
    ) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Уведомление можно только подтвердить доставкой';
    END IF;
END//

CREATE TRIGGER tn_integration_outbox_attempts_no_update BEFORE UPDATE ON tn_integration_outbox_attempts
FOR EACH ROW BEGIN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Попытку доставки физического факта нельзя изменять';
END//

CREATE TRIGGER tn_integration_outbox_attempts_no_delete BEFORE DELETE ON tn_integration_outbox_attempts
FOR EACH ROW BEGIN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Попытку доставки физического факта нельзя удалять';
END//

CREATE TRIGGER tn_notification_outbox_no_delete BEFORE DELETE ON tn_notification_outbox
FOR EACH ROW BEGIN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Уведомление нельзя удалять';
END//

CREATE TRIGGER tn_notification_attempts_no_update BEFORE UPDATE ON tn_notification_attempts
FOR EACH ROW BEGIN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Попытку уведомления нельзя изменять';
END//

CREATE TRIGGER tn_notification_attempts_no_delete BEFORE DELETE ON tn_notification_attempts
FOR EACH ROW BEGIN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Попытку уведомления нельзя удалять';
END//

DELIMITER ;
