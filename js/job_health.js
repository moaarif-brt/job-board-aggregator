export const MIN_JOB_HEALTH_SCORE = 70;

let healthGateEnabled = false;
let minJobHealthScore = MIN_JOB_HEALTH_SCORE;

export function configureJobHealthGate(verification = {}) {
    healthGateEnabled = verification.enabled === true;
    minJobHealthScore = Number(verification.min_health_score || MIN_JOB_HEALTH_SCORE);
}

export function isHighConfidenceJob(job) {
    if (!job) return false;
    if (!healthGateEnabled) return true;
    if (job.verification_status !== 'active') return false;
    const score = Number(job.job_health_score || 0);
    return score >= minJobHealthScore;
}
