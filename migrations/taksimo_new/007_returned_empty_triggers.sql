-- Версия 2 полного цикла вагона: возврат порожних (триггер переходов).
-- Триггер задаёт допустимые переходы статусов вагона, включая возврат порожних
-- и замыкание цикла (returned_empty -> forming при следующей загрузке блока).
-- DDL-часть (колонки и ограничения) — в 007_returned_empty.sql.
-- Суффикс _triggers нужен, чтобы штатный мигратор применил этот файл через
-- локальную root-сессию с SET SESSION sql_log_bin = 0 (не приложением).

DROP TRIGGER IF EXISTS tn_wagons_full_lifecycle_only;

DELIMITER //

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
                AND NEW.returned_empty_at IS NULL
                AND NEW.returned_empty_by_operator_id IS NULL
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
                AND NEW.returned_empty_at IS NULL
                AND NEW.returned_empty_by_operator_id IS NULL
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
                AND NEW.returned_empty_at IS NULL
                AND NEW.returned_empty_by_operator_id IS NULL
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
                AND NEW.returned_empty_at IS NULL
                AND NEW.returned_empty_by_operator_id IS NULL
            ) OR (
                OLD.status = 'unloaded_bts_east'
                AND NEW.status = 'returned_empty'
                AND NEW.loaded_at <=> OLD.loaded_at
                AND NEW.loaded_by_operator_id <=> OLD.loaded_by_operator_id
                AND NEW.dispatched_at <=> OLD.dispatched_at
                AND NEW.dispatched_by_operator_id <=> OLD.dispatched_by_operator_id
                AND NEW.arrived_kodar_at <=> OLD.arrived_kodar_at
                AND NEW.arrived_kodar_by_operator_id <=> OLD.arrived_kodar_by_operator_id
                AND NEW.unloaded_bts_east_at <=> OLD.unloaded_bts_east_at
                AND NEW.unloaded_bts_east_by_operator_id <=> OLD.unloaded_bts_east_by_operator_id
                AND OLD.returned_empty_at IS NULL
                AND NEW.returned_empty_at IS NOT NULL
                AND OLD.returned_empty_by_operator_id IS NULL
                AND NEW.returned_empty_by_operator_id IS NOT NULL
            ) OR (
                OLD.status = 'returned_empty'
                AND NEW.status = 'forming'
                AND NEW.loaded_at IS NULL
                AND NEW.loaded_by_operator_id IS NULL
                AND NEW.dispatched_at IS NULL
                AND NEW.dispatched_by_operator_id IS NULL
                AND NEW.arrived_kodar_at IS NULL
                AND NEW.arrived_kodar_by_operator_id IS NULL
                AND NEW.unloaded_bts_east_at IS NULL
                AND NEW.unloaded_bts_east_by_operator_id IS NULL
                AND NEW.returned_empty_at IS NULL
                AND NEW.returned_empty_by_operator_id IS NULL
            )
        )
    ) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Недопустимый переход статуса вагона';
    END IF;
END//

DELIMITER ;