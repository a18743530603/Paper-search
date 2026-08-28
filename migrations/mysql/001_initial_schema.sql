-- Paper Hunter Business Database schema for MySQL 8.0+.
-- LangGraph checkpoint tables intentionally remain in the separate SQLite database.

CREATE TABLE IF NOT EXISTS papers (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    query TEXT NOT NULL,
    title TEXT NOT NULL,
    authors TEXT NOT NULL,
    summary LONGTEXT NOT NULL,
    published VARCHAR(128) NOT NULL,
    source VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    publisher VARCHAR(512) NOT NULL DEFAULT '',
    doi VARCHAR(512) NULL,
    page_url VARCHAR(2048) NOT NULL,
    pdf_url VARCHAR(2048) NULL,
    local_path VARCHAR(2048) NULL,
    status VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    error TEXT NULL,
    downloaded_at DATETIME(6) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    KEY idx_papers_created_at (created_at DESC),
    KEY idx_papers_query (query(255))
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS research_tasks (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    question LONGTEXT NOT NULL,
    max_results INT UNSIGNED NOT NULL,
    sources_json LONGTEXT NOT NULL DEFAULT ('["arxiv", "crossref"]'),
    plan_goal TEXT NOT NULL,
    plan_queries_json LONGTEXT NOT NULL,
    plan_rationale TEXT NOT NULL DEFAULT (''),
    plan_model VARCHAR(255) NOT NULL DEFAULT '',
    planner_status VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    planner_error TEXT NULL,
    checkpoint_thread_id VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NULL,
    status VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    paper_ids_json LONGTEXT NOT NULL DEFAULT ('[]'),
    result_count INT UNSIGNED NOT NULL DEFAULT 0,
    selected_paper_ids_json LONGTEXT NOT NULL DEFAULT ('[]'),
    selected_count INT UNSIGNED NOT NULL DEFAULT 0,
    report_question LONGTEXT NOT NULL DEFAULT (''),
    conversation_summary LONGTEXT NOT NULL DEFAULT (''),
    summary_up_to_message_id BIGINT UNSIGNED NOT NULL DEFAULT 0,
    conversation_summary_error TEXT NULL,
    conversation_summary_updated_at DATETIME(6) NULL,
    task_memory_error TEXT NULL,
    task_memory_updated_at DATETIME(6) NULL,
    error TEXT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    confirmed_at DATETIME(6) NULL,
    selected_at DATETIME(6) NULL,
    completed_at DATETIME(6) NULL,
    PRIMARY KEY (id),
    UNIQUE KEY idx_research_tasks_checkpoint_thread (checkpoint_thread_id),
    KEY idx_research_tasks_created_at (created_at DESC, id DESC)
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS pdf_text_cache (
    source_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    page_number INT UNSIGNED NOT NULL,
    content LONGTEXT NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (source_hash, page_number)
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS embedding_cache (
    cache_namespace VARCHAR(512) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    content_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    vector_json LONGTEXT NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (cache_namespace, content_hash)
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS llm_response_cache (
    cache_namespace VARCHAR(512) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    input_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    response_json LONGTEXT NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (cache_namespace, input_hash)
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS evaluation_runs (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    name VARCHAR(255) NOT NULL,
    status VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    chunk_strategy VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    query_mode VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL DEFAULT 'original',
    keyword_retriever VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL DEFAULT 'tfidf',
    reranker VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL DEFAULT 'none',
    chunk_size INT UNSIGNED NOT NULL,
    chunk_overlap INT UNSIGNED NOT NULL,
    embedding_model VARCHAR(255) NOT NULL,
    semantic_weight DOUBLE NOT NULL,
    keyword_weight DOUBLE NOT NULL,
    top_k INT UNSIGNED NOT NULL,
    case_count INT UNSIGNED NOT NULL DEFAULT 0,
    metrics_json LONGTEXT NULL,
    error TEXT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    completed_at DATETIME(6) NULL,
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS paper_documents (
    paper_id BIGINT UNSIGNED NOT NULL,
    parse_status VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL DEFAULT 'not_started',
    page_count INT UNSIGNED NOT NULL DEFAULT 0,
    chunk_count INT UNSIGNED NOT NULL DEFAULT 0,
    error TEXT NULL,
    source_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
    chunk_strategy VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
    chunk_size INT UNSIGNED NULL,
    chunk_overlap INT UNSIGNED NULL,
    layout_stats_json LONGTEXT NULL,
    parser_backend VARCHAR(128) NULL,
    parser_model VARCHAR(255) NULL,
    parser_job_id VARCHAR(255) NULL,
    parse_stage VARCHAR(128) NULL,
    parsed_at DATETIME(6) NULL,
    index_status VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL DEFAULT 'not_indexed',
    index_error TEXT NULL,
    vectorizer VARCHAR(255) NULL,
    embedding_namespace VARCHAR(512) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NULL,
    indexed_at DATETIME(6) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (paper_id),
    CONSTRAINT fk_paper_documents_paper
        FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS paper_parent_chunks (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    paper_id BIGINT UNSIGNED NOT NULL,
    page_number INT UNSIGNED NOT NULL,
    parent_index INT UNSIGNED NOT NULL,
    content LONGTEXT NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uq_paper_parent_chunks_paper_index (paper_id, parent_index),
    CONSTRAINT fk_paper_parent_chunks_paper
        FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS paper_chunks (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    paper_id BIGINT UNSIGNED NOT NULL,
    page_number INT UNSIGNED NOT NULL,
    chunk_index INT UNSIGNED NOT NULL,
    content LONGTEXT NOT NULL,
    parent_chunk_id BIGINT UNSIGNED NULL,
    chunk_role VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL DEFAULT 'retrieval',
    section_title TEXT NOT NULL DEFAULT (''),
    content_type VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL DEFAULT 'text',
    bbox_json LONGTEXT NULL,
    vector_json LONGTEXT NULL,
    dense_vector_json LONGTEXT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uq_paper_chunks_paper_index (paper_id, chunk_index),
    CONSTRAINT fk_paper_chunks_paper
        FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE,
    CONSTRAINT fk_paper_chunks_parent
        FOREIGN KEY (parent_chunk_id) REFERENCES paper_parent_chunks(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS rag_queries (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    paper_id BIGINT UNSIGNED NOT NULL,
    question LONGTEXT NOT NULL,
    reranker VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL DEFAULT 'none',
    rerank_error TEXT NULL,
    answer LONGTEXT NULL,
    evidence_json LONGTEXT NULL,
    model VARCHAR(255) NULL,
    status VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    error TEXT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    KEY idx_rag_queries_paper_id (paper_id, id DESC),
    CONSTRAINT fk_rag_queries_paper
        FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS evaluation_cases (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    paper_id BIGINT UNSIGNED NOT NULL,
    question_type VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    question LONGTEXT NOT NULL,
    question_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin
        GENERATED ALWAYS AS (SHA2(question, 256)) STORED,
    reference_answer LONGTEXT NOT NULL,
    evidence_page INT UNSIGNED NOT NULL,
    evidence_text LONGTEXT NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uq_evaluation_cases_paper_question_hash (paper_id, question_hash),
    KEY idx_evaluation_cases_paper_id (paper_id, id),
    CONSTRAINT fk_evaluation_cases_paper
        FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS research_tool_calls (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    task_id BIGINT UNSIGNED NOT NULL,
    call_index INT UNSIGNED NOT NULL,
    tool_name VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    input_json LONGTEXT NOT NULL,
    status VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    result_count INT UNSIGNED NOT NULL DEFAULT 0,
    source_counts_json LONGTEXT NOT NULL DEFAULT ('{}'),
    error TEXT NULL,
    duration_ms BIGINT UNSIGNED NOT NULL DEFAULT 0,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uq_research_tool_calls_task_call (task_id, call_index),
    CONSTRAINT fk_research_tool_calls_task
        FOREIGN KEY (task_id) REFERENCES research_tasks(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS agent_traces (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    task_id BIGINT UNSIGNED NULL,
    thread_id VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    node_name VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    status VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    start_time DATETIME(6) NOT NULL,
    end_time DATETIME(6) NULL,
    duration_ms BIGINT UNSIGNED NULL,
    tool_calls_json LONGTEXT NOT NULL DEFAULT ('[]'),
    model_calls INT UNSIGNED NULL,
    input_tokens BIGINT UNSIGNED NULL,
    output_tokens BIGINT UNSIGNED NULL,
    retry_count INT UNSIGNED NOT NULL DEFAULT 0,
    error TEXT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    KEY idx_agent_traces_task_id (task_id, id),
    KEY idx_agent_traces_thread_node (thread_id, node_name, id),
    CONSTRAINT fk_agent_traces_task
        FOREIGN KEY (task_id) REFERENCES research_tasks(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS research_reports (
    task_id BIGINT UNSIGNED NOT NULL,
    status VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL DEFAULT 'not_started',
    content LONGTEXT NULL,
    details_json LONGTEXT NOT NULL DEFAULT ('{}'),
    model VARCHAR(255) NULL,
    error TEXT NULL,
    started_at DATETIME(6) NULL,
    completed_at DATETIME(6) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (task_id),
    CONSTRAINT fk_research_reports_task
        FOREIGN KEY (task_id) REFERENCES research_tasks(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS research_messages (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    task_id BIGINT UNSIGNED NOT NULL,
    role VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    phase VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    content LONGTEXT NOT NULL,
    status VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL DEFAULT 'completed',
    evidence_json LONGTEXT NOT NULL DEFAULT ('[]'),
    model VARCHAR(255) NULL,
    error TEXT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    KEY idx_research_messages_task_id (task_id, id),
    CONSTRAINT fk_research_messages_task
        FOREIGN KEY (task_id) REFERENCES research_tasks(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS research_task_memories (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    research_task_id BIGINT UNSIGNED NOT NULL,
    category VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    content LONGTEXT NOT NULL,
    status VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL DEFAULT 'active',
    created_from_message_id BIGINT UNSIGNED NOT NULL,
    updated_from_message_id BIGINT UNSIGNED NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    KEY idx_research_task_memories_task_status (research_task_id, status, id),
    CONSTRAINT fk_research_task_memories_task
        FOREIGN KEY (research_task_id) REFERENCES research_tasks(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS background_jobs (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    job_type VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    payload_json LONGTEXT NOT NULL DEFAULT ('{}'),
    dedupe_key VARCHAR(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    paper_id BIGINT UNSIGNED NULL,
    rag_query_id BIGINT UNSIGNED NULL,
    research_task_id BIGINT UNSIGNED NULL,
    status VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL DEFAULT 'queued',
    active_dedupe_key VARCHAR(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin
        GENERATED ALWAYS AS (
            CASE
                WHEN status IN ('queued', 'running', 'retry_wait') THEN dedupe_key
                ELSE NULL
            END
        ) STORED,
    priority INT NOT NULL DEFAULT 0,
    attempt_count INT UNSIGNED NOT NULL DEFAULT 0,
    max_attempts INT UNSIGNED NOT NULL DEFAULT 3,
    available_at DATETIME(6) NOT NULL,
    lease_expires_at DATETIME(6) NULL,
    worker_id VARCHAR(255) NULL,
    last_error TEXT NULL,
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    started_at DATETIME(6) NULL,
    completed_at DATETIME(6) NULL,
    PRIMARY KEY (id),
    UNIQUE KEY idx_background_jobs_active_dedupe (active_dedupe_key),
    KEY idx_background_jobs_ready (status, available_at, priority DESC, id),
    KEY idx_background_jobs_rag_query (rag_query_id, id DESC),
    CONSTRAINT fk_background_jobs_paper
        FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE,
    CONSTRAINT fk_background_jobs_rag_query
        FOREIGN KEY (rag_query_id) REFERENCES rag_queries(id) ON DELETE CASCADE,
    CONSTRAINT fk_background_jobs_research_task
        FOREIGN KEY (research_task_id) REFERENCES research_tasks(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS evaluation_results (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    run_id BIGINT UNSIGNED NOT NULL,
    case_id BIGINT UNSIGNED NOT NULL,
    hit_rank INT UNSIGNED NULL,
    reciprocal_rank DOUBLE NOT NULL DEFAULT 0,
    best_coverage DOUBLE NOT NULL DEFAULT 0,
    retrieved_json LONGTEXT NOT NULL,
    error TEXT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uq_evaluation_results_run_case (run_id, case_id),
    CONSTRAINT fk_evaluation_results_run
        FOREIGN KEY (run_id) REFERENCES evaluation_runs(id) ON DELETE CASCADE,
    CONSTRAINT fk_evaluation_results_case
        FOREIGN KEY (case_id) REFERENCES evaluation_cases(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS generation_evaluation_runs (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    name VARCHAR(255) NOT NULL,
    status VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    retrieval_run_id BIGINT UNSIGNED NOT NULL,
    generator_model VARCHAR(255) NOT NULL,
    judge_model VARCHAR(255) NOT NULL,
    prompt_version VARCHAR(128) NOT NULL,
    case_count INT UNSIGNED NOT NULL DEFAULT 0,
    metrics_json LONGTEXT NULL,
    error TEXT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    completed_at DATETIME(6) NULL,
    PRIMARY KEY (id),
    CONSTRAINT fk_generation_evaluation_runs_retrieval_run
        FOREIGN KEY (retrieval_run_id) REFERENCES evaluation_runs(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS generation_evaluation_results (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    run_id BIGINT UNSIGNED NOT NULL,
    case_id BIGINT UNSIGNED NOT NULL,
    answer LONGTEXT NULL,
    evidence_json LONGTEXT NOT NULL DEFAULT ('[]'),
    faithfulness DOUBLE NOT NULL DEFAULT 0,
    answer_relevance DOUBLE NOT NULL DEFAULT 0,
    reference_correctness DOUBLE NOT NULL DEFAULT 0,
    citation_correctness DOUBLE NOT NULL DEFAULT 0,
    judge_json LONGTEXT NOT NULL DEFAULT ('{}'),
    generation_seconds DOUBLE NOT NULL DEFAULT 0,
    judge_seconds DOUBLE NOT NULL DEFAULT 0,
    error TEXT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uq_generation_evaluation_results_run_case (run_id, case_id),
    CONSTRAINT fk_generation_evaluation_results_run
        FOREIGN KEY (run_id) REFERENCES generation_evaluation_runs(id) ON DELETE CASCADE,
    CONSTRAINT fk_generation_evaluation_results_case
        FOREIGN KEY (case_id) REFERENCES evaluation_cases(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
