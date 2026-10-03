-- Версия 2 полного цикла вагона: возврат порожних (DDL-часть).
-- Цикл (одни и те же вагоны ходят по кругу):
--   Таксимо: forming -> loaded -> in_transit -> at_kodar -> unloaded_bts_east
--   -> returned_empty (порожние вернулись на площадку).
-- История каждого прохода сохраняется в tn_events; tn_wagons хранит текущий
-- проход. Данные из старой SQLite-Таксимо сюда не пишутся и не читаются.
-- Триггер допустимых переходов статусов — в 007_returned_empty_triggers.sql.

ALTER TABLE tn_wagons
    DROP CHECK chk_tn_wagons_status,
    ADD COLUMN returned_empty_at DATETIME(6) NULL AFTER unloaded_bts_east_by_operator_id,
    ADD COLUMN returned_empty_by_operator_id BIGINT UNSIGNED NULL AFTER returned_empty_at;

ALTER TABLE tn_wagons
    ADD CONSTRAINT chk_tn_wagons_status
        CHECK (status IN ('forming', 'loaded', 'in_transit', 'at_kodar', 'unloaded_bts_east', 'returned_empty')),
    ADD CONSTRAINT fk_tn_wagons_returned_empty_by
        FOREIGN KEY (returned_empty_by_operator_id) REFERENCES tn_operator_accounts(id) ON DELETE RESTRICT;