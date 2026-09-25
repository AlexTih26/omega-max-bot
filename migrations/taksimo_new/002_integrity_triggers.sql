-- Триггеры неизменяемости новой PWA Таксимо.
-- Эта миграция выполняется только локальным MySQL root-сеансом с отключённым
-- для него sql_log_bin: приложение и мигратор не получают лишних привилегий.

DELIMITER //

CREATE TRIGGER tn_events_no_update BEFORE UPDATE ON tn_events
FOR EACH ROW BEGIN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'События новой Таксимо неизменяемы';
END//

CREATE TRIGGER tn_events_no_delete BEFORE DELETE ON tn_events
FOR EACH ROW BEGIN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'События новой Таксимо нельзя удалять';
END//

CREATE TRIGGER tn_login_attempts_no_update BEFORE UPDATE ON tn_operator_login_attempts
FOR EACH ROW BEGIN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Журнал входов нельзя изменять';
END//

CREATE TRIGGER tn_login_attempts_no_delete BEFORE DELETE ON tn_operator_login_attempts
FOR EACH ROW BEGIN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Журнал входов нельзя удалять';
END//

CREATE TRIGGER tn_expected_intake_lines_no_update BEFORE UPDATE ON tn_expected_intake_lines
FOR EACH ROW BEGIN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Ожидаемый состав приёмки нельзя изменять';
END//

CREATE TRIGGER tn_expected_intake_lines_no_delete BEFORE DELETE ON tn_expected_intake_lines
FOR EACH ROW BEGIN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Ожидаемый состав приёмки нельзя удалять';
END//

CREATE TRIGGER tn_intake_lines_no_update BEFORE UPDATE ON tn_intake_lines
FOR EACH ROW BEGIN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Строку фактической приёмки нельзя изменять';
END//

CREATE TRIGGER tn_intake_lines_no_delete BEFORE DELETE ON tn_intake_lines
FOR EACH ROW BEGIN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Строку фактической приёмки нельзя удалять';
END//

CREATE TRIGGER tn_wagon_loads_no_update BEFORE UPDATE ON tn_wagon_loads
FOR EACH ROW BEGIN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Подтверждённую загрузку вагона нельзя изменять';
END//

CREATE TRIGGER tn_wagon_loads_no_delete BEFORE DELETE ON tn_wagon_loads
FOR EACH ROW BEGIN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Подтверждённую загрузку вагона нельзя удалять';
END//

CREATE TRIGGER tn_cancellations_no_update BEFORE UPDATE ON tn_intake_cancellations
FOR EACH ROW BEGIN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Отмену нельзя изменять';
END//

CREATE TRIGGER tn_cancellations_no_delete BEFORE DELETE ON tn_intake_cancellations
FOR EACH ROW BEGIN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Отмену нельзя удалять';
END//

CREATE TRIGGER tn_corrections_no_update BEFORE UPDATE ON tn_intake_corrections
FOR EACH ROW BEGIN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Корректировку нельзя изменять';
END//

CREATE TRIGGER tn_corrections_no_delete BEFORE DELETE ON tn_intake_corrections
FOR EACH ROW BEGIN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Корректировку нельзя удалять';
END//

CREATE TRIGGER tn_operation_corrections_no_update BEFORE UPDATE ON tn_operation_corrections
FOR EACH ROW BEGIN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Корректировку нельзя изменять';
END//

CREATE TRIGGER tn_operation_corrections_no_delete BEFORE DELETE ON tn_operation_corrections
FOR EACH ROW BEGIN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Корректировку нельзя удалять';
END//

CREATE TRIGGER tn_attachments_no_update BEFORE UPDATE ON tn_attachments
FOR EACH ROW BEGIN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Архив вложения нельзя изменять';
END//

CREATE TRIGGER tn_attachments_no_delete BEFORE DELETE ON tn_attachments
FOR EACH ROW BEGIN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Архив вложения нельзя удалять';
END//

CREATE TRIGGER tn_integration_inbox_no_update BEFORE UPDATE ON tn_integration_inbox
FOR EACH ROW BEGIN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Входящее сообщение интеграции нельзя изменять';
END//

CREATE TRIGGER tn_integration_inbox_no_delete BEFORE DELETE ON tn_integration_inbox
FOR EACH ROW BEGIN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Входящее сообщение интеграции нельзя удалять';
END//

CREATE TRIGGER tn_integration_outbox_ack_only BEFORE UPDATE ON tn_integration_outbox
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
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Исходящее сообщение можно только подтвердить';
    END IF;
END//

CREATE TRIGGER tn_integration_outbox_no_delete BEFORE DELETE ON tn_integration_outbox
FOR EACH ROW BEGIN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Исходящее сообщение интеграции нельзя удалять';
END//

CREATE TRIGGER tn_final_intakes_no_update BEFORE UPDATE ON tn_intakes
FOR EACH ROW BEGIN
    IF OLD.status IN ('confirmed', 'discrepancy') THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Подтверждённую приёмку нельзя изменять';
    END IF;
END//

CREATE TRIGGER tn_final_intakes_no_delete BEFORE DELETE ON tn_intakes
FOR EACH ROW BEGIN
    IF OLD.status IN ('confirmed', 'discrepancy') THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Подтверждённую приёмку нельзя удалять';
    END IF;
END//

CREATE TRIGGER tn_blocks_yard_insert_only BEFORE INSERT ON tn_blocks
FOR EACH ROW BEGIN
    IF NEW.current_location_kind <> 'yard'
       OR NEW.yard_x IS NULL
       OR NEW.yard_y IS NULL
       OR NEW.yard_slot IS NULL
       OR NEW.wagon_number IS NOT NULL THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Новый блок должен быть размещён на площадке';
    END IF;
END//

CREATE TRIGGER tn_blocks_move_to_wagon_only BEFORE UPDATE ON tn_blocks
FOR EACH ROW BEGIN
    IF NOT (
        NEW.id <=> OLD.id
        AND NEW.block_type <=> OLD.block_type
        AND NEW.block_number <=> OLD.block_number
        AND NEW.product_name <=> OLD.product_name
        AND NEW.weight_kg <=> OLD.weight_kg
        AND NEW.condition_code <=> OLD.condition_code
        AND NEW.received_intake_id <=> OLD.received_intake_id
        AND NEW.received_line_id <=> OLD.received_line_id
        AND NEW.created_at <=> OLD.created_at
        AND OLD.current_location_kind = 'yard'
        AND NEW.current_location_kind = 'wagon'
        AND NEW.yard_x IS NULL
        AND NEW.yard_y IS NULL
        AND NEW.yard_slot IS NULL
        AND NEW.wagon_number IS NOT NULL
        AND NEW.wagon_number <> ''
    ) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Блок можно только переместить с площадки в вагон';
    END IF;
END//

CREATE TRIGGER tn_blocks_no_delete BEFORE DELETE ON tn_blocks
FOR EACH ROW BEGIN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Принятый блок нельзя удалять';
END//

CREATE TRIGGER tn_wagons_dispatch_only BEFORE UPDATE ON tn_wagons
FOR EACH ROW BEGIN
    IF NOT (
        NEW.id <=> OLD.id
        AND NEW.wagon_number <=> OLD.wagon_number
        AND NEW.created_at <=> OLD.created_at
        AND OLD.status = 'loading'
        AND NEW.status = 'dispatched'
        AND OLD.dispatched_at IS NULL
        AND NEW.dispatched_at IS NOT NULL
        AND OLD.dispatched_by_operator_id IS NULL
        AND NEW.dispatched_by_operator_id IS NOT NULL
    ) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Вагон можно только один раз отправить';
    END IF;
END//

CREATE TRIGGER tn_wagons_no_delete BEFORE DELETE ON tn_wagons
FOR EACH ROW BEGIN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Вагон нельзя удалять из журнала';
END//

DELIMITER ;
