CREATE TABLE IF NOT EXISTS paper_summaries (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    task_id BIGINT UNSIGNED NOT NULL,
    paper_id BIGINT UNSIGNED NOT NULL,
    status VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL DEFAULT 'queued',
    content LONGTEXT NULL,
    evidence_json LONGTEXT NOT NULL DEFAULT ('[]'),
    model VARCHAR(255) NULL,
    error TEXT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    completed_at DATETIME(6) NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_paper_summaries_task_paper (task_id, paper_id),
    KEY idx_paper_summaries_paper_id (paper_id, id DESC),
    CONSTRAINT fk_paper_summaries_task
        FOREIGN KEY (task_id) REFERENCES research_tasks(id) ON DELETE CASCADE,
    CONSTRAINT fk_paper_summaries_paper
        FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
