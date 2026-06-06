// ============================================================
// FILTERING
// ============================================================

import { loadApplicationStatus } from './storage.js';
import { isHighConfidenceJob } from './job_health.js';

function normalizeText(value) {
    return String(value || '')
        .normalize('NFKD')
        .replace(/[\u0300-\u036f]/g, '')
        .toLowerCase()
        .trim();
}

function splitTerms(value) {
    return normalizeText(value)
        .split(/[\s,]+/)
        .map(term => term.trim())
        .filter(Boolean);
}

function matchesAllTerms(text, terms) {
    if (!terms.length) return true;
    const normalized = normalizeText(text);
    return terms.every(term => normalized.includes(term));
}

function matchesAnyTerm(text, terms) {
    if (!terms.length) return true;
    const normalized = normalizeText(text);
    return terms.some(term => normalized.includes(term));
}

/**
 * Read current filter values from the DOM.
 * @returns {object} Filter state object
 */
export function readFilterInputs() {
    return {
        hideRecruiters: document.getElementById('filter-hide-recruiters').checked,
        remoteOnly: document.getElementById('filter-remote-only').checked,
        hideApplied: document.getElementById('filter-hide-applied').checked,
        title: normalizeText(document.getElementById('filter-title').value),
        company: normalizeText(document.getElementById('filter-company').value),
        location: normalizeText(document.getElementById('filter-location').value),
        salary: document.getElementById('filter-salary-min').value,
        status: document.getElementById('filter-status').value,
        ats: document.getElementById('filter-ats').value,
        skill_level: document.getElementById('filter-skill-level').value,
        exclude: normalizeText(document.getElementById('filter-exclude').value),
        include: normalizeText(document.getElementById('filter-include').value),
    };
}

/**
 * Filter the full jobs array based on the current filter inputs.
 * @param {Array} allJobs - The complete jobs array
 * @returns {{ filteredJobs: Array, filterState: object }}
 */
export function filterJobs(allJobs) {
    const f = readFilterInputs();
    const apps = loadApplicationStatus();

    const titleTerms = splitTerms(f.title);
    const companyTerms = splitTerms(f.company);
    const locationTerms = splitTerms(f.location);
    const excludeTerms = splitTerms(f.exclude);
    const includeTerms = splitTerms(f.include);

    const filterState = {
        title: f.title,
        company: f.company,
        location: f.location,
        salary: f.salary,
        remoteOnly: f.remoteOnly,
        status: f.status,
        ats: f.ats,
        skill_level: f.skill_level,
        exclude: f.exclude,
        include: f.include
    };

    const filteredJobs = allJobs.filter(job => {
        if (!isHighConfidenceJob(job)) return false;

        // Recruiter filter
        if (f.hideRecruiters && job.is_recruiter === true) return false;

        // Application status
        const url = job.url;
        const jobStatus = apps[url]?.status || '';

        if (f.hideApplied && (jobStatus === 'applied' || jobStatus === 'ignored')) return false;
        if (f.status && jobStatus !== f.status) return false;

        // Text fields
        const title = normalizeText(job.title);
        const company = normalizeText((job.company || job.company_slug) || '');
        let location = '';
        if (job.location) {
            location = typeof job.location === 'object'
                ? normalizeText(job.location.name)
                : normalizeText(job.location);
        }

        // in your filter state collection
        const minSalary = parseInt(document.getElementById('filter-salary-min').value) || 0;

        // in filteredJobs
        if (minSalary > 0) {
            const median = job.salary?.median;
            if (!median || median < minSalary) return false;
        }

        // Remote only
        if (f.remoteOnly) {
            const isRemote = location.includes('remote')
                || (job.workplaceType && job.workplaceType.toLowerCase() === 'remote');
            if (!isRemote) return false;
        }

        // ATS
        if (f.ats) {
            const jobAts = (job.ats || '').toLowerCase();
            if (jobAts !== f.ats.toLowerCase()) return false;
        }

        // Skill level
        if (f.skill_level) {
            const jobSkillLevel = (job.skill_level || '').toLowerCase();
            if (jobSkillLevel !== f.skill_level.toLowerCase()) return false;
        }

        // Exclude title keywords
        if (excludeTerms.length && matchesAnyTerm(title, excludeTerms)) return false;

        // Include Title keywords
        if (includeTerms.length && !matchesAnyTerm(title, includeTerms)) return false;

        return (
            matchesAllTerms(title, titleTerms) &&
            matchesAllTerms(company, companyTerms) &&
            matchesAllTerms(location, locationTerms)
        );
    });

    return { filteredJobs, filterState };
}

/** Reset all filter DOM inputs to defaults */
export function clearFilterInputs() {
    document.getElementById('filter-title').value = '';
    document.getElementById('filter-company').value = '';
    document.getElementById('filter-location').value = '';
    document.getElementById('filter-salary-min').value = '';
    document.getElementById('filter-exclude').value = '';
    document.getElementById('filter-include').value = '';
    document.getElementById('filter-status').value = '';
    document.getElementById('filter-ats').value = '';
    document.getElementById('filter-skill-level').value = '';
    document.getElementById('filter-hide-recruiters').checked = true;
    document.getElementById('filter-remote-only').checked = false;
    document.getElementById('filter-hide-applied').checked = false;
}
