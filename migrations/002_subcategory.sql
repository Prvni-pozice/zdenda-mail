-- Migrace 002: subkategorie pro `unimportant` (banks/energie/eshops/develop/sw/doprava/komora).
-- Verze pravidel: v2-subcategories-2026-05-12 (viz src/zdenda_mail/rules.py).

ALTER TABLE classifications ADD COLUMN subcategory TEXT;
CREATE INDEX IF NOT EXISTS idx_class_subcategory ON classifications(subcategory);
