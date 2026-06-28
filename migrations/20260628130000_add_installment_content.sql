-- Sub-project B: store each run's synthesized thread installment (the verified
-- whats_new / resolved / new_questions JSON) on its thread_installments row, so the
-- rendering stage (sub-project C) can present "what's new today" without re-synthesizing.

ALTER TABLE thread_installments ADD COLUMN content TEXT;
