-- 016: Триггеры append-only и контролируемого завершения идемпотентной команды.
-- Этот файл выполняется штатным runner отдельной локальной root-сессией с
-- отключённым sql_log_bin. Приложение не получает требуемые для CREATE TRIGGER
-- привилегии.

DELIMITER //

CREATE TRIGGER tn_action_log_no_update BEFORE UPDATE ON tn_action_log
FOR EACH ROW BEGIN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Аудит действий нельзя изменять';
END//

CREATE TRIGGER tn_action_log_no_delete BEFORE DELETE ON tn_action_log
FOR EACH ROW BEGIN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Аудит действий нельзя удалять';
END//

CREATE TRIGGER tn_vehicle_queue_transitions_no_update
BEFORE UPDATE ON tn_vehicle_queue_transitions
FOR EACH ROW BEGIN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Историю переходов очереди нельзя изменять';
END//

CREATE TRIGGER tn_vehicle_queue_transitions_no_delete
BEFORE DELETE ON tn_vehicle_queue_transitions
FOR EACH ROW BEGIN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Историю переходов очереди нельзя удалять';
END//

CREATE TRIGGER tn_vehicle_queue_entries_no_delete
BEFORE DELETE ON tn_vehicle_queue_entries
FOR EACH ROW BEGIN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Запись очереди нельзя удалять';
END//

CREATE TRIGGER tn_command_idempotency_pending_insert_only
BEFORE INSERT ON tn_command_idempotency
FOR EACH ROW BEGIN
    IF NOT (
        NEW.status = 'pending'
        AND NEW.initial_response_status IS NULL
        AND NEW.response_body_json IS NULL
        AND NEW.completed_at IS NULL
    ) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Команду можно создать только в состоянии pending';
    END IF;
END//

CREATE TRIGGER tn_command_idempotency_pending_to_completed_only
BEFORE UPDATE ON tn_command_idempotency
FOR EACH ROW BEGIN
    IF NOT (
        NEW.id <=> OLD.id
        AND NEW.actor_account_id <=> OLD.actor_account_id
        AND NEW.operation_code <=> OLD.operation_code
        AND NEW.idempotency_key <=> OLD.idempotency_key
        AND NEW.fingerprint_algorithm <=> OLD.fingerprint_algorithm
        AND NEW.fingerprint_version <=> OLD.fingerprint_version
        AND NEW.request_fingerprint <=> OLD.request_fingerprint
        AND NEW.created_at <=> OLD.created_at
        AND OLD.status = 'pending'
        AND NEW.status = 'completed'
        AND OLD.initial_response_status IS NULL
        AND NEW.initial_response_status = 201
        AND OLD.response_body_json IS NULL
        AND NEW.response_body_json IS NOT NULL
        AND OLD.completed_at IS NULL
        AND NEW.completed_at IS NOT NULL
        AND NEW.completed_at >= OLD.created_at
    ) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Команду можно только завершить без изменения её области и отпечатка';
    END IF;
END//

CREATE TRIGGER tn_command_idempotency_no_delete
BEFORE DELETE ON tn_command_idempotency
FOR EACH ROW BEGIN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Запись идемпотентности нельзя удалять';
END//

DELIMITER ;
