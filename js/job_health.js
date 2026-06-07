export const MIN_JOB_HEALTH_SCORE = 90;

let minJobHealthScore = MIN_JOB_HEALTH_SCORE;

export function configureJobHealthGate(verification = {}) {
    minJobHealthScore = Number(verification.min_health_score || MIN_JOB_HEALTH_SCORE);
}

export function isHighConfidenceJob(job) {
    if (!job) return false;
    if (job.verification_status !== 'active') return false;
    const score = Number(job.job_health_score || 0);
    return score >= minJobHealthScore;
}
