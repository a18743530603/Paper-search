-- Phase 5 indexes backed by representative MySQL EXPLAIN plans.
-- Keep the existing queue-ready and active-dedupe indexes unchanged.

ALTER TABLE research_messages
    ADD KEY idx_research_messages_followup_recent (
        task_id, phase, status, created_at DESC, id DESC
    );

ALTER TABLE background_jobs
    ADD KEY idx_background_jobs_task_type_recent (
        research_task_id, job_type, id DESC
    ),
    ADD KEY idx_background_jobs_paper_type_recent (
        paper_id, job_type, id DESC
    );
