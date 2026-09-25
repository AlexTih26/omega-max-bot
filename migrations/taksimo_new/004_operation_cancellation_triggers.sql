-- Не допускает изменения или удаления причин отмены подтверждённых операций.

DELIMITER //

CREATE TRIGGER tn_operation_cancellations_no_update BEFORE UPDATE ON tn_operation_cancellations
FOR EACH ROW BEGIN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Отмену подтверждённой операции нельзя изменять';
END//

CREATE TRIGGER tn_operation_cancellations_no_delete BEFORE DELETE ON tn_operation_cancellations
FOR EACH ROW BEGIN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Отмену подтверждённой операции нельзя удалять';
END//

DELIMITER ;
